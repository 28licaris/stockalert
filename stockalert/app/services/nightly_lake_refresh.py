"""
Nightly Polygon flat-files → stock-lake (S3) refresh.

Background asyncio loop: sleep until ``LAKE_ARCHIVE_RUN_HOUR_UTC``, then
archive **yesterday's** Polygon aggregates for ``NIGHTLY_LAKE_SYMBOLS``
(default: seed universe). ClickHouse OHLCV is not written — only the
lake sink + watermark rows.

Gating: ``LAKE_ARCHIVE_ENABLED``, non-empty ``STOCK_LAKE_BUCKET``, and
working Polygon flat-files credentials.
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
from app.services.flatfiles_sinks import LakeSink
from app.services.lake_archive import LakeArchiveWriter
from app.services.s3_lake_client import S3LakeClient

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
    if not settings.lake_archive_enabled:
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
        "nightly_lake_refresh: invalid NIGHTLY_LAKE_KIND=%r; using 'minute'",
        raw,
    )
    return ("minute",)


async def refresh_polygon_lake_yesterday(
    *,
    target: date | None = None,
    kinds: tuple[str, ...] | None = None,
) -> dict[str, Any]:
    """
    One-shot: Polygon flat files for ``target`` (default: yesterday UTC)
    into the stock lake only, with watermark-based skip for idempotency.
    """
    if not _nightly_lake_gated():
        return {"skipped": True, "reason": "lake disabled or STOCK_LAKE_BUCKET empty"}

    d = target or (date.today() - timedelta(days=1))
    sym_spec = settings.nightly_lake_symbols
    symbols = resolve_nightly_lake_symbols(sym_spec)
    kind_tuple = kinds if kinds is not None else _parse_nightly_kind(settings.nightly_lake_kind)

    try:
        client = PolygonFlatFilesClient.from_settings()
    except Exception as e:
        logger.error("nightly_lake_refresh: Polygon flat-files client: %s", e)
        return {"skipped": True, "reason": f"polygon client: {e}"}

    source_tag = FlatFilesBackfillService.DEFAULT_SOURCE_TAG
    writer = LakeArchiveWriter(
        s3=S3LakeClient.from_settings(),
        watermarks=WatermarkRepo.from_clickhouse(),
    )
    sinks = [LakeSink(writer=writer, force=False)]
    svc = FlatFilesBackfillService(
        flat_files=client,
        sinks=sinks,
        source_tag=source_tag,
    )

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
        logger.info("nightly_lake_refresh: kind=%s summary=%s", kind, out[kind])

    return out


async def run_lake_refresh_loop(stop_event: asyncio.Event | None = None) -> None:
    """
    Sleep until ``LAKE_ARCHIVE_RUN_HOUR_UTC`` each day, then run
    ``refresh_polygon_lake_yesterday``. Errors are logged; the loop continues.
    """
    ev = stop_event or asyncio.Event()
    hour = int(getattr(settings, "lake_archive_run_hour_utc", 7) or 7)

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
