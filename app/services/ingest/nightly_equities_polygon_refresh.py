"""
Nightly Polygon flat-files → `equities.polygon_raw` refresh.

Background asyncio loop: sleep until ``POLYGON_NIGHTLY_RUN_HOUR_UTC``,
then archive **yesterday's** Polygon aggregates for
``POLYGON_NIGHTLY_SYMBOLS`` (default: whole market). ClickHouse OHLCV
is not written by this loop — only the v2 equities Iceberg sink.

Gating: ``POLYGON_NIGHTLY_ENABLED``, non-empty ``STOCK_LAKE_BUCKET``,
and working Polygon flat-files credentials.

v2 cutover (CV7): writes to `equities.polygon_raw` via
`EquitiesIcebergSink.for_polygon_raw()`. The v1 path
(`bronze.polygon_minute` via `BronzeIcebergSink.for_polygon_minute`)
is gone — the operator must have run the Phase 1A bulk-load
(CV4 operational step, `scripts/polygon_history_backfill.py`) before
this code path starts producing nightly diffs, otherwise
`equities.polygon_raw` will be empty until the catch-up loop fills
the lookback window. v1 branch retains the bronze-targeted code for
rollback.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.config import settings
from app.providers.polygon_flatfiles import PolygonFlatFilesClient
from app.services.equities.sink import EquitiesIcebergSink
from app.services.ingest.flatfiles_backfill import FlatFilesBackfillService

logger = logging.getLogger(__name__)


def _seconds_until_next_run(hour_utc: int, *, now: datetime | None = None) -> float:
    """Seconds until the next ``hour_utc``:00 UTC boundary (in (0, 86400])."""
    now = now or datetime.now(timezone.utc)
    h = max(0, min(23, int(hour_utc)))
    target = now.replace(hour=h, minute=0, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def resolve_nightly_lake_symbols(spec: str) -> list[str]:
    """Translate ``POLYGON_NIGHTLY_SYMBOLS`` → list[str].

    Spec strings:
      - "all" / "*" / ""    → empty list (= whole-market via flat-files;
                              Polygon flat-files contain every symbol
                              regardless of input, so the empty list is
                              the "import everything" signal)
      - "active"            → authoritative ClickHouse stream_universe
      - "AAPL,NVDA,…"       → explicit list (uppercased)
    """
    s = (spec or "").strip().lower()
    if s in ("all", "*", ""):
        return []
    from app.services.universe import resolve_universe_spec

    return resolve_universe_spec(spec)


def _nightly_lake_gated() -> bool:
    if not settings.polygon_nightly_enabled:
        return False
    if not (settings.stock_lake_bucket or "").strip():
        return False
    return True


def _parse_nightly_kind(raw: str) -> tuple[str, ...]:
    """Return one or two flat-file kinds: ``minute``, ``day``, or both."""
    s = (raw or "minute").strip().lower()
    if s in ("minute", "min", "1m"):
        return ("minute",)
    if s in ("day", "daily", "d"):
        return ("day",)
    if s in ("both", "all", "minute+day", "minute_day"):
        return ("minute", "day")
    logger.warning(
        "nightly_lake_refresh: invalid POLYGON_NIGHTLY_KIND=%r; using 'minute'",
        raw,
    )
    return ("minute",)


async def refresh_polygon_lake_yesterday(
    *,
    target: date | None = None,
    kinds: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """
    Polygon flat files → `equities.polygon_raw`.

    Behavior:
      - ``target`` set    → process exactly that calendar day (CLI / tests).
      - ``target`` None   → AUTO-CATCHUP. Find the most recent date already
                            in equities.polygon_raw and fill every weekday
                            from then through yesterday. If the table is
                            empty in the last 14 days, processes yesterday
                            only (cold-start fallback).

    Each day is processed in sequence. Idempotency is enforced by the
    pre-scan against `equities.polygon_raw` (skip_dates) so re-runs are
    no-ops on already-archived dates — single source of truth, no CH
    watermark dependency.
    """
    if not _nightly_lake_gated():
        return {"skipped": True, "reason": "lake disabled or STOCK_LAKE_BUCKET empty"}

    # Build the list of dates to process.
    if target is not None:
        dates_to_process: list[date] = [target]
    else:
        from app.services.equities.gaps import (
            missing_weekdays,
            yesterday_et,
        )
        from app.services.equities.tables import ensure_polygon_raw
        try:
            equities_table = ensure_polygon_raw()
            dates_to_process = missing_weekdays(equities_table)
        except Exception as e:
            logger.warning(
                "nightly_lake_refresh: gap detection failed (%s); "
                "falling back to yesterday-only",
                e,
            )
            yesterday = yesterday_et()
            dates_to_process = [yesterday] if yesterday.weekday() < 5 else []

        if not dates_to_process:
            logger.info(
                "nightly_lake_refresh: no gaps to fill — "
                "equities.polygon_raw is up to date"
            )
            return {"skipped": True, "reason": "no gaps; equities.polygon_raw up to date"}
        logger.info(
            "nightly_lake_refresh: catch-up covers %d day(s): %s",
            len(dates_to_process),
            [d.isoformat() for d in dates_to_process],
        )

    sym_spec = settings.polygon_nightly_symbols
    symbols = resolve_nightly_lake_symbols(sym_spec)
    kind_tuple = kinds if kinds is not None else _parse_nightly_kind(settings.polygon_nightly_kind)

    try:
        client = PolygonFlatFilesClient.from_settings()
    except Exception as e:
        logger.error("nightly_lake_refresh: Polygon flat-files client: %s", e)
        return {"skipped": True, "reason": f"polygon client: {e}"}

    source_tag = FlatFilesBackfillService.DEFAULT_SOURCE_TAG
    # v2 (Phase 1B): single sink, equities.polygon_raw. Schwab and
    # corp_actions land in CV8 / CV9 respectively.
    equities_sink = EquitiesIcebergSink.for_polygon_raw()
    sinks = [equities_sink]
    svc = FlatFilesBackfillService(
        flat_files=client,
        sinks=sinks,
        source_tag=source_tag,
    )

    per_day_results: list[dict[str, Any]] = []
    for d in dates_to_process:
        out: dict[str, Any] = {"date": d.isoformat(), "kinds": list(kind_tuple)}
        for kind in kind_tuple:
            # Per-day skip pre-scan against the actual target table.
            # Same approach as scripts/polygon_history_backfill.py
            # (CV3) — no CH watermark dependency.
            skip_dates: set[date] = set()
            try:
                from app.services.equities.gaps import loaded_dates_in_range
                from app.services.equities.tables import ensure_polygon_raw
                skip_dates = loaded_dates_in_range(
                    ensure_polygon_raw(), start=d, end=d,
                )
            except Exception as e:
                logger.warning(
                    "nightly_lake_refresh: skip pre-scan failed (%s); "
                    "continuing without skip set",
                    e,
                )

            result = await svc.backfill_range(
                symbols,
                d,
                d,
                kind=kind,  # type: ignore[arg-type]
                dry_run=False,
                concurrency=1,
                skip_dates=skip_dates,
            )
            out[kind] = result.to_summary()
            logger.info("nightly_lake_refresh: date=%s kind=%s summary=%s", d, kind, out[kind])

        per_day_results.append(out)

    # NO SILENT FAILURES: if every processed day errored and nothing was
    # persisted, this run is a FAILURE (e.g. flat-files credentials 403),
    # not an "ok" with quietly-zero output. Raising marks the audit_run
    # as error so the health page surfaces it.
    def _day_failed(r: dict[str, Any]) -> bool:
        kinds_res = [r[k] for k in r.get("kinds", []) if isinstance(r.get(k), dict)]
        return bool(kinds_res) and all(
            kr.get("days_errored", 0) > 0 and kr.get("bars_persisted", 0) == 0
            for kr in kinds_res
        )

    if per_day_results and all(_day_failed(r) for r in per_day_results):
        raise RuntimeError(
            f"nightly polygon refresh: ALL {len(per_day_results)} day(s) errored with "
            f"0 bars persisted — check Polygon flat-files S3 credentials "
            f"(403 Forbidden = expired key or lapsed subscription)"
        )

    # Backwards-compatible return shape: when called with explicit `target`,
    # callers expect the single-day dict. Auto-catchup returns a wrapper.
    if target is not None:
        return per_day_results[0] if per_day_results else {"skipped": True, "reason": "no date processed"}
    return {
        "auto_catchup": True,
        "days_processed": len(per_day_results),
        "dates": [r["date"] for r in per_day_results],
        "results": per_day_results,
    }


async def run_lake_refresh_loop(stop_event: asyncio.Event | None = None) -> None:
    """
    Sleep until ``POLYGON_NIGHTLY_RUN_HOUR_UTC`` each day, then run
    ``refresh_polygon_lake_yesterday``. Errors are logged; the loop continues.
    """
    ev = stop_event or asyncio.Event()
    hour = int(getattr(settings, "polygon_nightly_run_hour_utc", 7) or 7)

    while not ev.is_set():
        wait_s = _seconds_until_next_run(hour)
        logger.info(
            "nightly_lake_refresh: next run at %02d:00 UTC in %.2fh",
            hour,
            wait_s / 3600.0,
        )
        try:
            await asyncio.wait_for(ev.wait(), timeout=wait_s)
            return
        except asyncio.TimeoutError:
            pass
        if ev.is_set():
            return

        try:
            from app.services.jobs.service import audit_run
            async with audit_run("nightly_equities_polygon_refresh") as rec:
                rec.result = await refresh_polygon_lake_yesterday()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("nightly_lake_refresh: iteration failed")
