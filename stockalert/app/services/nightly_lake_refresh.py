"""
Nightly Polygon flat-files → bronze.polygon_minute refresh.

Background asyncio loop: sleep until ``POLYGON_NIGHTLY_RUN_HOUR_UTC``,
then archive **yesterday's** Polygon aggregates for
``POLYGON_NIGHTLY_SYMBOLS`` (default: seed universe). ClickHouse OHLCV
is not written by this loop — only the bronze Iceberg sink.

Gating: ``POLYGON_NIGHTLY_ENABLED``, non-empty ``STOCK_LAKE_BUCKET``,
and working Polygon flat-files credentials.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.config import settings
from app.data.seed_universe import SEED_SYMBOLS
from app.db.lake_watermarks import WatermarkRepo
from app.providers.polygon_flatfiles import PolygonFlatFilesClient
from app.services.flatfiles_backfill import FlatFilesBackfillService
from app.services.bronze import BronzeIcebergSink

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
    """Same semantics as ``polygon_flatfiles_bulk_backfill._resolve_symbols``."""
    s = (spec or "").strip().lower()
    if s in ("seed", "seed-100", "seed_100"):
        return list(SEED_SYMBOLS)
    if s in ("all", "*", ""):
        return []
    return [tok.strip().upper() for tok in spec.split(",") if tok.strip()]


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
    Polygon flat files → bronze.polygon_minute.

    Behavior:
      - ``target`` set    → process exactly that calendar day (CLI / tests).
      - ``target`` None   → AUTO-CATCHUP. Find the most recent date already
                            in bronze and fill every weekday from then
                            through yesterday. If bronze is empty in the
                            last 14 days, processes yesterday only (cold-
                            start fallback to avoid runaway backfills).

    Each day is processed in sequence with watermark-based idempotency,
    so re-runs are no-ops on already-archived dates.
    """
    if not _nightly_lake_gated():
        return {"skipped": True, "reason": "lake disabled or STOCK_LAKE_BUCKET empty"}

    # Build the list of dates to process.
    if target is not None:
        dates_to_process: list[date] = [target]
    else:
        from app.services.bronze import (
            ensure_bronze_polygon_minute,
            latest_bronze_date,
            missing_weekdays,
        )
        try:
            bronze_table = ensure_bronze_polygon_minute()
            latest = latest_bronze_date(bronze_table)
            if latest is None:
                # Truly empty (or no data in lookback window) — cold-start.
                # Seed with yesterday only; operator can run a deeper
                # manual backfill if they want more history.
                yesterday = date.today() - timedelta(days=1)
                dates_to_process = [yesterday] if yesterday.weekday() < 5 else []
                logger.info(
                    "nightly_lake_refresh: bronze empty in lookback window — "
                    "cold-start with yesterday only"
                )
            else:
                # Normal catch-up path: fill (latest+1)..yesterday weekdays.
                dates_to_process = missing_weekdays(bronze_table)
        except Exception as e:
            logger.warning(
                "nightly_lake_refresh: gap detection failed (%s); "
                "falling back to yesterday-only",
                e,
            )
            from app.services.bronze import yesterday_et
            yesterday = yesterday_et()
            dates_to_process = [yesterday] if yesterday.weekday() < 5 else []

        if not dates_to_process:
            logger.info("nightly_lake_refresh: no gaps to fill — bronze is up to date")
            return {"skipped": True, "reason": "no gaps; bronze up to date"}
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
    # Bronze (Iceberg) is the canonical lake destination as of Phase 1.
    # The legacy `LakeSink` (raw/provider=*/...parquet) is no longer
    # written to — its watermark ledger is kept around for now as an
    # audit trail; Phase 4 retires it entirely.
    bronze_sink = BronzeIcebergSink.for_polygon_minute()
    sinks = [bronze_sink]
    svc = FlatFilesBackfillService(
        flat_files=client,
        sinks=sinks,
        source_tag=source_tag,
    )

    per_day_results: list[dict[str, Any]] = []
    for d in dates_to_process:
        out: dict[str, Any] = {"date": d.isoformat(), "kinds": list(kind_tuple)}
        for kind in kind_tuple:
            table_name = "ohlcv_1m" if kind == "minute" else "ohlcv_daily"
            skip_dates: set[date] = set()
            try:
                repo = WatermarkRepo.from_clickhouse()
                skip_dates = await repo.get_ok_dates(
                    source=source_tag,
                    table_name=table_name,
                    stage="raw",
                    start=d,
                    end=d,
                )
            except Exception as e:
                logger.warning(
                    "nightly_lake_refresh: watermark pre-scan failed (%s); "
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
            await refresh_polygon_lake_yesterday()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("nightly_lake_refresh: iteration failed")
