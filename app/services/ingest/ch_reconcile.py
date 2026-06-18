"""Scheduled ClickHouse reconcile from the ``equities.schwab_universe`` lake.

**Why.** The live Schwab WebSocket stream is ClickHouse's primary source,
but it's lossy: every server restart or stream outage drops bars (e.g. a
whole regular session missing while only after-hours landed —
NVDA-on-2026-06-17). ``equities.schwab_universe`` is the *authoritative,
complete* record — written each night by the Schwab REST refresh. This
job pushes that completeness back into CH so gaps self-heal without a
human re-running a sync by hand.

**Scope.** The ENTIRE active universe in ONE pass — it reads every symbol
present in the lookback window from ``schwab_universe`` (no per-symbol
loop), then bulk-inserts into ``stocks.ohlcv_1m``.

**Idempotent.** ``ohlcv_1m`` is a ReplacingMergeTree keyed on
(symbol, timestamp); the reconcile stamps a fresh (high) version, so bars
CH already has are deduped away at merge time and only the *missing* ones
add coverage. Safe to run repeatedly.

**Cadence.** Daily, post-close, AFTER ``nightly_schwab_refresh`` has
written the complete prior-day session to ``schwab_universe`` (default
23:00 UTC vs the nightly's 22:00). It guarantees *post-close*
completeness; mid-session gaps are handled on-demand by the bars gateway.

Reads the lake via PyIceberg using the app's own AWS creds — no S3
credentials are handed to ClickHouse (and ``schwab_universe`` is small —
month-partitioned, a handful of files — so the read is quick).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from app.config import settings

logger = logging.getLogger(__name__)

# ohlcv_1m insert column order (matches scripts/hotload_ch_from_lake.py).
_CH_COLUMNS = [
    "symbol", "timestamp", "open", "high", "low", "close",
    "volume", "vwap", "trade_count", "source", "version",
]

# Default an hour after nightly_schwab_refresh (22:00 UTC) so the lake
# already holds the complete prior-day session before we reconcile.
RECONCILE_DEFAULT_HOUR_UTC = 23


def reconcile_ch_from_schwab(lookback_days: int = 7) -> dict:
    """Push ``schwab_universe``'s last `lookback_days` (all symbols) into CH.

    Returns ``{rows, symbols, wall_s[, error]}``. Never raises — a lake-read
    or CH-insert failure is logged (NO silent failure) and returned so the
    background loop keeps running.
    """
    from pyiceberg.expressions import GreaterThanOrEqual

    from app.db.client import get_client
    from app.services.equities.schemas import equities_table_id
    from app.services.iceberg_catalog import get_catalog

    t0 = time.time()
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    try:
        table = get_catalog().load_table(equities_table_id("schwab_universe"))
        arr = table.scan(
            row_filter=GreaterThanOrEqual("timestamp", since.isoformat()),
        ).to_arrow()
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.error("ch_reconcile: schwab_universe read failed: %s", exc)
        return {"rows": 0, "symbols": 0, "wall_s": time.time() - t0, "error": str(exc)}

    if arr.num_rows == 0:
        logger.info(
            "ch_reconcile: no rows in schwab_universe over last %dd — nothing to do",
            lookback_days,
        )
        return {"rows": 0, "symbols": 0, "wall_s": time.time() - t0}

    rows = _arrow_to_ch_rows(arr)
    try:
        get_client().insert("stocks.ohlcv_1m", rows, column_names=_CH_COLUMNS)
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.error("ch_reconcile: CH insert failed (rows=%d): %s", len(rows), exc)
        return {"rows": 0, "symbols": 0, "wall_s": time.time() - t0, "error": str(exc)}

    import pyarrow.compute as pc

    n_symbols = pc.count_distinct(arr["symbol"]).as_py()
    wall = time.time() - t0
    logger.info(
        "ch_reconcile: synced %d rows / %d symbols from schwab_universe "
        "(last %dd) into CH in %.1fs",
        len(rows), n_symbols, lookback_days, wall,
    )
    return {"rows": len(rows), "symbols": n_symbols, "wall_s": wall}


def _arrow_to_ch_rows(arr) -> list[list]:
    """Multi-symbol Arrow → ohlcv_1m row list. source='lake-reconcile';
    version = now-ms so the authoritative lake bar wins any overlap with
    an earlier-inserted live-stream bar (same OHLCV; just re-tags)."""
    cols = arr.to_pydict()
    version = int(datetime.now(timezone.utc).timestamp() * 1000)
    out: list[list] = []
    for i in range(arr.num_rows):
        out.append([
            cols["symbol"][i],
            cols["timestamp"][i],
            float(cols["open"][i]) if cols["open"][i] is not None else 0.0,
            float(cols["high"][i]) if cols["high"][i] is not None else 0.0,
            float(cols["low"][i]) if cols["low"][i] is not None else 0.0,
            float(cols["close"][i]) if cols["close"][i] is not None else 0.0,
            float(cols["volume"][i]) if cols["volume"][i] is not None else 0.0,
            float(cols["vwap"][i]) if cols["vwap"][i] is not None else 0.0,
            int(round(cols["trade_count"][i])) if cols["trade_count"][i] is not None else 0,
            "lake-reconcile",
            version,
        ])
    return out


def _seconds_until_next_run(hour_utc: int, *, now: datetime | None = None) -> float:
    """Seconds until the next `hour_utc:00` UTC. Same shape as the nightly
    refresh's scheduler."""
    now = now or datetime.now(timezone.utc)
    h = max(0, min(23, int(hour_utc)))
    target = now.replace(hour=h, minute=0, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


async def run_ch_reconcile_loop() -> None:
    """Forever loop: sleep until the configured run hour, then reconcile."""
    if not getattr(settings, "ch_reconcile_enabled", False):
        logger.info("ch_reconcile: disabled (CH_RECONCILE_ENABLED=false)")
        return

    hour = getattr(settings, "ch_reconcile_run_hour_utc", RECONCILE_DEFAULT_HOUR_UTC)
    lookback = int(getattr(settings, "ch_reconcile_lookback_days", 7))
    logger.info(
        "ch_reconcile: loop started (CH_RECONCILE_RUN_HOUR_UTC=%s, lookback=%dd)",
        hour, lookback,
    )
    while True:
        try:
            wait_s = _seconds_until_next_run(hour)
            logger.info("ch_reconcile: sleeping %.0fs until next run", wait_s)
            await asyncio.sleep(wait_s)
            await asyncio.to_thread(reconcile_ch_from_schwab, lookback)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — keep the loop alive
            logger.exception("ch_reconcile: loop error: %s", exc)
            await asyncio.sleep(300)
