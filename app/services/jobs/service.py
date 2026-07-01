"""JobRegistry implementation — catalogs background loops, audits runs.

Per the locked design (FE-CONTRACTS-Status):

  - Each background loop registers itself at main_api startup, providing
    a stable `name` (used as `ingestion_runs.job_name`), a human-readable
    schedule string, and (optionally) a `run_now` callable.
  - `list()` joins the in-memory registry with last-success data from
    `ingestion_runs` (via `repo.fetch_last_runs`).
  - `run_now(name)` fires the callable in a background task behind a
    per-job `asyncio.Lock` so a scheduled run and a manual click can't
    overlap on the same job.

Audit:
  - `audit_run(job_name)` is a context manager that wraps any one-cycle
    function with `ingestion_runs` writes (started_at, finished_at,
    status, error). Use it when registering jobs whose one-cycle
    function does NOT already self-audit. Loops that already write
    their own row (e.g. `live_lake_writer.run_cycle`) should NOT be
    double-wrapped.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Awaitable, Callable, Optional, Union

from app.services.jobs.repo import fetch_last_runs
from app.services.jobs.schemas import JobMetadata, JobRunResult, RunResult

logger = logging.getLogger(__name__)


RunNowCallable = Callable[[], Union[None, Awaitable[None]]]


class _JobEntry:
    __slots__ = (
        "name",
        "display_name",
        "schedule",
        "setting_key",
        "run_now",
        "lock",
    )

    def __init__(
        self,
        *,
        name: str,
        display_name: str,
        schedule: str,
        setting_key: Optional[str],
        run_now: Optional[RunNowCallable],
    ) -> None:
        self.name = name
        self.display_name = display_name
        self.schedule = schedule
        self.setting_key = setting_key
        self.run_now = run_now
        # Per-job lock so scheduled-run + manual-run can't overlap.
        # Created lazily because asyncio.Lock binds to the running loop.
        self.lock: Optional[asyncio.Lock] = None

    def get_lock(self) -> asyncio.Lock:
        if self.lock is None:
            self.lock = asyncio.Lock()
        return self.lock


class JobRegistry:
    """Singleton (use the module-level `job_registry`).

    Thread-safe registration (a regular Lock guards the dict) and
    asyncio-safe per-job execution (each entry's lock is an
    asyncio.Lock).
    """

    def __init__(self) -> None:
        self._jobs: dict[str, _JobEntry] = {}
        self._lock = threading.Lock()

    # ──────────────────────────────────────────────────────────────────
    # Registration
    # ──────────────────────────────────────────────────────────────────

    def register(
        self,
        *,
        name: str,
        display_name: str,
        schedule: str,
        setting_key: Optional[str] = None,
        run_now: Optional[RunNowCallable] = None,
    ) -> None:
        if not name:
            raise ValueError("job name is required")
        with self._lock:
            self._jobs[name] = _JobEntry(
                name=name,
                display_name=display_name,
                schedule=schedule,
                setting_key=setting_key,
                run_now=run_now,
            )
        logger.info(
            "job_registry: registered %s (%s; runnable=%s)",
            name, schedule, run_now is not None,
        )

    # ──────────────────────────────────────────────────────────────────
    # Read
    # ──────────────────────────────────────────────────────────────────

    def list(self) -> list[JobMetadata]:
        runs = fetch_last_runs()
        with self._lock:
            entries = list(self._jobs.values())

        items: list[JobMetadata] = []
        for entry in entries:
            data = runs.get(entry.name) or {}
            running = self.is_running(entry.name)
            last_status = data.get("last_status")
            # Normalize unknown CH values + factor in the "running" state.
            if running:
                status = "running"
            elif last_status == "ok":
                status = "ok"
            elif last_status in ("error", "partial_fail"):
                status = "error"
            elif data.get("last_run_at"):
                status = data["last_status"] or "unknown"
            else:
                status = "idle"
            items.append(
                JobMetadata(
                    name=entry.name,
                    display_name=entry.display_name,
                    schedule=entry.schedule,
                    setting_key=entry.setting_key,
                    runnable=entry.run_now is not None,
                    last_success=data.get("last_success"),
                    last_run_at=data.get("last_run_at"),
                    last_status=status,
                    last_error=data.get("last_error"),
                    last_summary=data.get("last_summary"),
                    running=running,
                )
            )
        items.sort(key=lambda m: m.display_name.lower())
        return items

    def get(self, name: str) -> Optional[JobMetadata]:
        with self._lock:
            if name not in self._jobs:
                return None
        for m in self.list():
            if m.name == name:
                return m
        return None

    def is_running(self, name: str) -> bool:
        with self._lock:
            entry = self._jobs.get(name)
        if entry is None or entry.lock is None:
            return False
        return entry.lock.locked()

    # ──────────────────────────────────────────────────────────────────
    # Manual run
    # ──────────────────────────────────────────────────────────────────

    async def run_now(self, name: str) -> JobRunResult:
        """Trigger a manual run. Returns immediately; the actual run
        happens in a fire-and-forget asyncio task."""
        with self._lock:
            entry = self._jobs.get(name)
        if entry is None:
            return JobRunResult(
                job=name,
                status="not_found",
                detail=f"no job registered with name {name!r}",
            )
        if entry.run_now is None:
            return JobRunResult(
                job=name,
                status="not_runnable",
                detail="this job has no run_now callable registered",
            )
        if entry.get_lock().locked():
            return JobRunResult(
                job=name,
                status="already_running",
                detail="a run is already in flight for this job",
            )

        started_at = datetime.now(timezone.utc).isoformat()
        # Fire-and-forget. The task captures the lock + executes; we
        # return the started_at marker so the caller can correlate.
        asyncio.create_task(
            self._execute_run(name),
            name=f"job_run:{name}",
        )
        return JobRunResult(
            job=name,
            status="started",
            started_at=started_at,
        )

    async def _execute_run(self, name: str) -> RunResult:
        """Acquire the per-job lock + invoke the registered callable.

        Returns a result object regardless of outcome (no exceptions
        propagate) so background tasks don't crash the loop. Callers
        who want to know the outcome should poll `list()`.
        """
        with self._lock:
            entry = self._jobs.get(name)
        if entry is None or entry.run_now is None:
            return RunResult(
                job=name,
                started_at=datetime.now(timezone.utc).isoformat(),
                status="error",
                error="job not registered or not runnable",
            )

        async with entry.get_lock():
            started_at_dt = datetime.now(timezone.utc)
            started_at = started_at_dt.isoformat()
            try:
                fn = entry.run_now
                if asyncio.iscoroutinefunction(fn):
                    await fn()
                else:
                    # Sync callable: offload to the default executor so
                    # we don't block the event loop.
                    await asyncio.to_thread(fn)
            except Exception as exc:  # noqa: BLE001 — boundary
                logger.exception("job_run %s failed", name)
                return RunResult(
                    job=name,
                    started_at=started_at,
                    finished_at=datetime.now(timezone.utc).isoformat(),
                    status="error",
                    error=str(exc)[:500],
                )
            return RunResult(
                job=name,
                started_at=started_at,
                finished_at=datetime.now(timezone.utc).isoformat(),
                status="ok",
            )


# ─────────────────────────────────────────────────────────────────────
# Audit wrapper — for jobs whose one-cycle function doesn't already
# write to ingestion_runs.
# ─────────────────────────────────────────────────────────────────────


class RunRecorder:
    """Handle yielded by ``audit_run`` so the wrapped cycle can report what
    it did. Set ``rec.result`` to the cycle's return dict; ``audit_run``
    derives a concise ``summary`` + ``rows_written`` from it.
    """

    __slots__ = ("result",)

    def __init__(self) -> None:
        self.result: object = None


# Per-job date (UTC) of the last "heartbeat" row we wrote for a no-op cycle.
# Lets frequent jobs (news, options, journal) skip writing a row every poll
# while still proving-of-life once per day. In-memory is fine: on restart the
# first no-op cycle simply writes one fresh heartbeat.
_last_heartbeat_day: dict[str, str] = {}


def _summarize(result: object) -> tuple[int, str, bool]:
    """Return ``(rows_written, one_line_summary, is_noop)`` from a cycle's
    return value. Defensive across the heterogeneous dict shapes the various
    cycle functions return. ``is_noop`` marks "nothing happened" cycles so
    frequent jobs can suppress the log row.
    """
    if not isinstance(result, dict):
        return 0, "", False

    if result.get("skipped"):
        reason = str(result.get("reason") or "skipped")
        return 0, f"skipped: {reason}"[:200], True

    rows = int(result.get("rows_written") or result.get("rows") or 0)

    # Nightly auto-catchup shape: {days_processed, dates, ...}
    if "days_processed" in result:
        days = int(result.get("days_processed") or 0)
        dates = result.get("dates") or []
        summ = f"filled {days} session(s)"
        if dates:
            summ += ": " + ", ".join(str(d) for d in dates[:10])
        return rows, summ[:200], days == 0

    # Single-day nightly shape: {date, symbols, exit_code}
    if "date" in result and "symbols" in result:
        summ = f"{result['date']}: {result['symbols']} symbol(s) (rc={result.get('exit_code')})"
        return rows, summ[:200], False

    # News shape: {stored, enriched, fomc_stored, econ_releases, ...}
    if "stored" in result or "enriched" in result:
        stored = int(result.get("stored") or 0)
        enriched = int(result.get("enriched") or 0)
        fomc = int(result.get("fomc_stored") or 0)
        econ = int(result.get("econ_releases") or 0)
        parts = [f"stored={stored}", f"enriched={enriched}"]
        if fomc:
            parts.append(f"fomc={fomc}")
        if econ:
            parts.append(f"econ={econ}")
        noop = (stored == 0 and enriched == 0 and fomc == 0 and econ == 0)
        return (rows or stored + enriched), " ".join(parts), noop

    # Generic: compact "k=v" of the scalar keys. No-op when every numeric
    # counter is zero (nothing was written/changed this cycle).
    scalars = {k: v for k, v in result.items() if isinstance(v, (int, float, str, bool))}
    summ = " ".join(f"{k}={v}" for k, v in list(scalars.items())[:6])
    numeric = [v for v in scalars.values() if isinstance(v, (int, float)) and not isinstance(v, bool)]
    noop = rows == 0 and (not numeric or all(n == 0 for n in numeric))
    return rows, summ[:200], noop


@contextlib.asynccontextmanager
async def audit_run(
    job_name: str,
    *,
    frequent: bool = False,
    window_start: Optional[datetime] = None,
    window_end: Optional[datetime] = None,
):
    """Context manager wrapping a one-cycle function with `ingestion_runs`
    writes. This is the SINGLE auditing mechanism — both the scheduled loop
    and the manual "Run now" button route every job through it so runs are
    recorded identically.

    Usage (capture the cycle's result for a concise summary)::

        async with audit_run("nightly_futures_refresh") as rec:
            rec.result = await refresh_futures_yesterday()

    ``frequent=True`` (news, options, journal) suppresses the log row for
    no-op cycles — writing at most one "heartbeat" row per UTC day — so a
    poll every few minutes doesn't flood the runs log. Real work and errors
    are always written.

    The wrapper:
      - generates a `run_id` (uuid4)
      - captures started_at / finished_at
      - derives `summary` + `rows_written` from `rec.result` via `_summarize`
      - writes status='ok' on clean exit, status='error' on exception
        (and re-raises so callers see the failure)
      - tolerates CH write failures (logs but doesn't block the caller)
    """
    started_at = datetime.now(timezone.utc)
    run_id = str(uuid.uuid4())
    error_msg = ""
    rec = RunRecorder()
    try:
        yield rec
    except Exception as exc:  # noqa: BLE001 — boundary
        error_msg = str(exc)[:500]
        raise
    finally:
        finished_at = datetime.now(timezone.utc)
        rows_written, summary, is_noop = _summarize(rec.result)
        status = "error" if error_msg else "ok"

        # Frequent jobs: skip no-op, non-error cycles unless we haven't
        # written a heartbeat yet today.
        skip_write = False
        if frequent and is_noop and not error_msg:
            day = finished_at.date().isoformat()
            if _last_heartbeat_day.get(job_name) == day:
                skip_write = True
            else:
                _last_heartbeat_day[job_name] = day
                summary = summary or "heartbeat: no new work"

        if not skip_write:
            try:
                from app.db.client import get_client

                client = get_client()
                client.insert(
                    "ingestion_runs",
                    [[
                        run_id,
                        job_name,
                        started_at,
                        finished_at,
                        window_start or started_at,
                        window_end or finished_at,
                        rows_written,
                        "{}",                    # per_provider_rows_written_json
                        error_msg,               # per_provider_errors_json (the error)
                        summary,
                        status,
                    ]],
                    column_names=[
                        "run_id",
                        "job_name",
                        "started_at",
                        "finished_at",
                        "window_start",
                        "window_end",
                        "rows_written",
                        "per_provider_rows_written_json",
                        "per_provider_errors_json",
                        "summary",
                        "status",
                    ],
                )
            except Exception as exc:  # noqa: BLE001 — CH unavailable
                logger.warning(
                    "audit_run %s: ingestion_runs write failed: %s", job_name, exc,
                )


# Module-level singleton — matches the StreamService / WatchlistService pattern.
job_registry = JobRegistry()
