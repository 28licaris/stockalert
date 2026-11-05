"""Nightly jobs: the lake completeness guard and the ClickHouse state snapshot.

Both are in-process asyncio loops like the other nightlies, audited through
``audit_run`` so they appear on the Health page and in ``ingestion_runs``, and
both have a manual "run now". A failure raises, so the run is recorded as an
error instead of passing quietly.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable

from app.config import settings

logger = logging.getLogger(__name__)

GUARD_JOB = "lake_completeness_guard"
SNAPSHOT_JOB = "ch_state_snapshot"


def seconds_until_next_run(hour_utc: int, *, now: datetime | None = None) -> float:
    now = now or datetime.now(timezone.utc)
    target = now.replace(hour=max(0, min(23, int(hour_utc))), minute=0, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def gate(job: str) -> tuple[bool, str]:
    """(enabled, reason-if-not)."""
    flag = settings.lake_guard_enabled if job == GUARD_JOB else settings.ch_state_snapshot_enabled
    name = "LAKE_GUARD_ENABLED" if job == GUARD_JOB else "CH_STATE_SNAPSHOT_ENABLED"
    if not flag:
        return False, f"{name}=false"
    if not (settings.stock_lake_bucket or "").strip():
        return False, "STOCK_LAKE_BUCKET is empty"
    return True, ""


async def run_guard_once() -> dict:
    """Archive anything the last ``lake_guard_lookback_days`` days hold that the lake lacks."""
    from app.services.lake_integrity.schemas import FAMILIES
    from app.services.lake_integrity.service import LakeArchive, default_window

    since, until = default_window(datetime.now(timezone.utc).date(), settings.lake_guard_lookback_days)
    archive = LakeArchive.from_settings()
    missing = archived = 0
    errors: list[str] = []
    for fam in FAMILIES.values():
        rep = await asyncio.to_thread(archive.run, fam, since=since, until=until, apply=True)
        missing += rep.missing_total
        archived += rep.archived_total
        errors += [f"{fam.name} {e}" for e in rep.errors]
    if missing:
        logger.warning(
            "%s: the lake was missing %d bar(s) from the last %d days; archived %d. "
            "The live writer should have written them.", GUARD_JOB, missing,
            settings.lake_guard_lookback_days, archived,
        )
    if errors or archived < missing:
        raise RuntimeError(f"{GUARD_JOB}: {archived}/{missing} archived; errors: {'; '.join(errors[:5])}")
    return {"rows_written": archived, "missing_found": missing, "lookback_days": settings.lake_guard_lookback_days}


async def run_snapshot_once() -> dict:
    from app.services.lake_integrity.snapshot import StateSnapshot

    snap = StateSnapshot.from_settings()
    m = await asyncio.to_thread(snap.export)
    if not m.complete:
        raise RuntimeError(f"{SNAPSHOT_JOB}: incomplete export, failed={m.failed}")
    pruned = await asyncio.to_thread(snap.prune, keep=settings.ch_state_snapshot_keep)
    return {
        "rows_written": sum(t.rows for t in m.tables),
        "tables": len(m.tables), "files": len(m.files), "pruned": len(pruned),
    }


async def _loop(job: str, hour_utc: int, once: Callable[[], Awaitable[dict]]) -> None:
    from app.services.jobs import audit_run

    logger.info("%s: loop armed (run hour %02d:00 UTC)", job, hour_utc)
    while True:
        try:
            wait = seconds_until_next_run(hour_utc)
            logger.info("%s: sleeping %.0fs until next run", job, wait)
            await asyncio.sleep(wait)
            async with audit_run(job) as rec:
                rec.result = await once()
        except asyncio.CancelledError:
            logger.info("%s: loop cancelled", job)
            raise
        except Exception as exc:  # noqa: BLE001 - recorded by audit_run; the loop outlives one bad night
            logger.exception("%s: run failed: %s", job, exc)
            await asyncio.sleep(300)


def start_jobs() -> list[asyncio.Task]:
    """Start the enabled loops and register them with the job registry. Returns the tasks."""
    from app.services.jobs import audit_run, job_registry

    specs = [
        (GUARD_JOB, "Lake completeness guard", settings.lake_guard_run_hour_utc, "LAKE_GUARD_RUN_HOUR_UTC", run_guard_once),
        (SNAPSHOT_JOB, "ClickHouse state snapshot", settings.ch_state_snapshot_run_hour_utc, "CH_STATE_SNAPSHOT_RUN_HOUR_UTC", run_snapshot_once),
    ]
    tasks: list[asyncio.Task] = []
    for job, title, hour, setting_key, once in specs:
        ok, why = gate(job)
        if not ok:
            logger.info("%s: not started: %s", job, why)
            continue

        async def _run_now(_job=job, _once=once) -> None:
            async with audit_run(_job) as rec:
                rec.result = await _once()

        tasks.append(asyncio.create_task(_loop(job, hour, once), name=job))
        job_registry.register(
            name=job, display_name=title, schedule=f"daily at {int(hour):02d}:00 UTC",
            setting_key=setting_key, run_now=_run_now,
        )
    return tasks
