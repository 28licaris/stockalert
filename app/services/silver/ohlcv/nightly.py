"""
Nightly silver OHLCV build loop — bronze → silver.ohlcv_1m + bar_quality.

Background asyncio loop: sleep until ``SILVER_OHLCV_BUILD_RUN_HOUR_UTC``,
then run `SilverOhlcvBuild.run_nightly()` (= yesterday × active universe).

Scheduling intent: this runs AFTER both upstream nightlies complete:
  - nightly_polygon_refresh: default 07:00 UTC
  - nightly_schwab_refresh:  default 22:00 UTC
  - silver_ohlcv_build:      default 23:00 UTC   ← here

So the bronze tables for yesterday are settled before silver merges
them. If a runner re-orders these manually, the build is still safe:
silver_ohlcv_build is idempotent and re-running it the next night
would catch up any partial result.

Gating: `SILVER_OHLCV_BUILD_ENABLED=true` + Iceberg/Glue config present.

Failure isolation: any single nightly run that raises is logged + the
loop waits the full sleep before retrying. Doesn't take the FastAPI
process down with it.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.config import settings
from app.data.seed_universe import SEED_SYMBOLS
from app.services.silver.ohlcv.build import SilverOhlcvBuild

logger = logging.getLogger(__name__)


SILVER_OHLCV_BUILD_DEFAULT_HOUR_UTC = 23  # 1h after Schwab nightly (22 UTC)


def _seconds_until_next_run(hour_utc: int, *, now: Optional[datetime] = None) -> float:
    """Same scheduling helper used by other nightlies (kept in this
    module so this loop has no dependency on a sibling).

    Sleeps until the next occurrence of `hour_utc:00` UTC. If we're
    already past that hour today, target tomorrow.
    """
    now = now or datetime.now(timezone.utc)
    h = max(0, min(23, int(hour_utc)))
    target = now.replace(hour=h, minute=0, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


def _resolve_symbols(spec: str) -> list[str]:
    """Translate config string → list[str]. "seed" → SEED_SYMBOLS."""
    s = (spec or "").strip().lower()
    if s in ("seed", "seed-100", "seed_100", ""):
        return list(SEED_SYMBOLS)
    return [tok.strip().upper() for tok in spec.split(",") if tok.strip()]


def _silver_build_gated() -> tuple[bool, str]:
    """Return (gated_off, reason). gated_off=True → loop should not run."""
    if not getattr(settings, "silver_ohlcv_build_enabled", False):
        return True, "SILVER_OHLCV_BUILD_ENABLED=false"
    if not (settings.stock_lake_bucket or "").strip():
        return True, "STOCK_LAKE_BUCKET is empty"
    if not (settings.iceberg_glue_database or "").strip():
        return True, "ICEBERG_GLUE_DATABASE is empty"
    return False, ""


async def run_silver_ohlcv_build_nightly() -> dict:
    """One-shot: run SilverOhlcvBuild.run_nightly() and return a summary.

    Wraps the synchronous SilverOhlcvBuild (PyIceberg writes are
    blocking) in `asyncio.to_thread` so the event loop stays
    responsive during the build (which can take minutes for a wide
    universe).
    """
    gated, why = _silver_build_gated()
    if gated:
        logger.info("nightly_silver_ohlcv_build: skipping — %s", why)
        return {"skipped": True, "reason": why}

    sym_spec = getattr(settings, "silver_ohlcv_build_symbols", "seed")
    symbols = _resolve_symbols(sym_spec)
    logger.info(
        "nightly_silver_ohlcv_build: starting (symbols=%d)", len(symbols),
    )

    def _build() -> dict:
        b = SilverOhlcvBuild.from_settings()
        result = b.run_nightly(symbols)
        return {
            "run_id": result.run_id,
            "started_at": result.started_at.isoformat(),
            "finished_at": result.finished_at.isoformat(),
            "duration_seconds": result.duration_seconds,
            "symbols": len(result.symbols),
            "slices": len(result.slices),
            "slices_succeeded": result.slices_succeeded,
            "slices_failed": result.slices_failed,
            "silver_rows": result.total_silver_rows,
        }

    summary = await asyncio.to_thread(_build)
    logger.info(
        "nightly_silver_ohlcv_build: done run_id=%s symbols=%d slices=%d "
        "(ok=%d fail=%d) rows=%d duration=%.1fs",
        summary["run_id"], summary["symbols"], summary["slices"],
        summary["slices_succeeded"], summary["slices_failed"],
        summary["silver_rows"], summary["duration_seconds"],
    )
    return summary


async def run_silver_ohlcv_build_loop() -> None:
    """Forever loop: sleep until configured run hour, then build."""
    gated, why = _silver_build_gated()
    if gated:
        logger.info("nightly_silver_ohlcv_build: loop not started — %s", why)
        return

    hour_utc = int(
        getattr(
            settings, "silver_ohlcv_build_run_hour_utc",
            SILVER_OHLCV_BUILD_DEFAULT_HOUR_UTC,
        )
    )
    logger.info(
        "nightly_silver_ohlcv_build: loop armed (run hour %02d:00 UTC)", hour_utc,
    )

    while True:
        try:
            wait_s = _seconds_until_next_run(hour_utc)
            logger.info(
                "nightly_silver_ohlcv_build: sleeping %.0fs until next run",
                wait_s,
            )
            await asyncio.sleep(wait_s)
            await run_silver_ohlcv_build_nightly()
        except asyncio.CancelledError:
            logger.info("nightly_silver_ohlcv_build: loop cancelled")
            raise
        except Exception as e:
            logger.exception("nightly_silver_ohlcv_build: unexpected error: %s", e)
            # Sleep before retrying so a persistent failure doesn't hot-loop.
            await asyncio.sleep(300)
