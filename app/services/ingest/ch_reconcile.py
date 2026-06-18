"""Scheduled ClickHouse reconcile from the authoritative Iceberg lakes.

**Why.** The live Schwab WebSocket stream is ClickHouse's primary source,
but it's lossy: every server restart or stream outage drops bars (e.g. a
whole regular session missing while only after-hours landed —
NVDA-on-2026-06-17). The nightly REST refresh writes the *authoritative,
complete* record to the lake; this job pushes that completeness back into
CH so gaps self-heal without a human re-running a sync by hand.

**Two asset classes, identical mechanism:**

  - Equities: ``equities.schwab_universe`` → ``stocks.ohlcv_1m``
  - Futures:  ``futures.schwab_futures``   → ``stocks.futures_ohlcv_1m``

Both lake tables share the canonical OHLCV column shape and both CH tables
are ReplacingMergeTree keyed on (symbol, timestamp), so one generic core
(`_reconcile_lake_to_ch`) handles both — the futures table simply has no
``adj_factor`` column, which the row builder never reads.

**Scope.** The ENTIRE active universe in ONE pass per table — it reads
every symbol present in the lookback window (no per-symbol loop), then
bulk-inserts.

**Idempotent.** The reconcile stamps a fresh (high) version, so bars CH
already has are deduped away at merge time and only the *missing* ones add
coverage. Safe to run repeatedly.

**Cadence.** Daily, post-close, AFTER the nightly refreshes have written
the complete prior-day sessions to the lakes (default 23:00 UTC vs the
nightlies' 22:00). Mid-session gaps are handled on-demand by the bars
gateway.

Reads the lakes via PyIceberg using the app's own AWS creds — no S3
credentials are handed to ClickHouse (and both tables are small —
month-partitioned, a handful of files — so the reads are quick).
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone

from app.config import settings

logger = logging.getLogger(__name__)

# ohlcv_1m / futures_ohlcv_1m insert column order (identical shape).
_CH_COLUMNS = [
    "symbol", "timestamp", "open", "high", "low", "close",
    "volume", "vwap", "trade_count", "source", "version",
]

# Default an hour after the nightly refreshes (22:00 UTC) so the lakes
# already hold the complete prior-day sessions before we reconcile.
RECONCILE_DEFAULT_HOUR_UTC = 23


def _reconcile_lake_to_ch(
    *,
    table,
    ch_table: str,
    label: str,
    lookback_days: int,
) -> dict:
    """Push a lake table's last `lookback_days` (all symbols) into a CH
    table. Generic core shared by the equities + futures reconciles.

    Returns ``{rows, symbols, wall_s[, error]}``. Never raises — a lake-read
    or CH-insert failure is logged (NO silent failure) and returned so the
    background loop keeps running.
    """
    from pyiceberg.expressions import GreaterThanOrEqual

    from app.db.client import get_client

    t0 = time.time()
    since = datetime.now(timezone.utc) - timedelta(days=lookback_days)

    try:
        arr = table.scan(
            row_filter=GreaterThanOrEqual("timestamp", since.isoformat()),
        ).to_arrow()
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.error("ch_reconcile[%s]: lake read failed: %s", label, exc)
        return {"rows": 0, "symbols": 0, "wall_s": time.time() - t0, "error": str(exc)}

    if arr.num_rows == 0:
        logger.info(
            "ch_reconcile[%s]: no rows over last %dd — nothing to do",
            label, lookback_days,
        )
        return {"rows": 0, "symbols": 0, "wall_s": time.time() - t0}

    rows = _arrow_to_ch_rows(arr, source=f"lake-reconcile-{label}")
    try:
        get_client().insert(ch_table, rows, column_names=_CH_COLUMNS)
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.error("ch_reconcile[%s]: CH insert failed (rows=%d): %s", label, len(rows), exc)
        return {"rows": 0, "symbols": 0, "wall_s": time.time() - t0, "error": str(exc)}

    import pyarrow.compute as pc

    n_symbols = pc.count_distinct(arr["symbol"]).as_py()
    wall = time.time() - t0
    logger.info(
        "ch_reconcile[%s]: synced %d rows / %d symbols (last %dd) into %s in %.1fs",
        label, len(rows), n_symbols, lookback_days, ch_table, wall,
    )
    return {"rows": len(rows), "symbols": n_symbols, "wall_s": wall}


def reconcile_ch_from_schwab(lookback_days: int = 7) -> dict:
    """Equities: push ``schwab_universe``'s last `lookback_days` into
    ``stocks.ohlcv_1m``."""
    from app.services.equities.schemas import equities_table_id
    from app.services.iceberg_catalog import get_catalog

    try:
        table = get_catalog().load_table(equities_table_id("schwab_universe"))
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.error("ch_reconcile[schwab_universe]: table load failed: %s", exc)
        return {"rows": 0, "symbols": 0, "wall_s": 0.0, "error": str(exc)}

    return _reconcile_lake_to_ch(
        table=table,
        ch_table="stocks.ohlcv_1m",
        label="schwab_universe",
        lookback_days=lookback_days,
    )


def reconcile_ch_from_futures(lookback_days: int = 7) -> dict:
    """Futures: push ``futures.schwab_futures``'s last `lookback_days` into
    ``stocks.futures_ohlcv_1m``. Ensures the lake table exists first (a
    fresh deployment that hasn't run the nightly yet just gets an empty
    scan, not a noisy NoSuchTable error)."""
    try:
        from app.services.futures.tables import ensure_schwab_futures
        table = ensure_schwab_futures()
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.error("ch_reconcile[schwab_futures]: table load failed: %s", exc)
        return {"rows": 0, "symbols": 0, "wall_s": 0.0, "error": str(exc)}

    return _reconcile_lake_to_ch(
        table=table,
        ch_table="stocks.futures_ohlcv_1m",
        label="schwab_futures",
        lookback_days=lookback_days,
    )


def _arrow_to_ch_rows(arr, *, source: str) -> list[list]:
    """Multi-symbol Arrow → CH ohlcv row list. ``version`` = now-ms so the
    authoritative lake bar wins any overlap with an earlier-inserted
    live-stream bar (same OHLCV; just re-tags). Reads only the canonical
    OHLCV columns, so it works for both equities and futures frames."""
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
            source,
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
    """Forever loop: sleep until the configured run hour, then reconcile
    BOTH equities and futures (each isolated — a failure in one is logged
    and the other still runs)."""
    if not getattr(settings, "ch_reconcile_enabled", False):
        logger.info("ch_reconcile: disabled (CH_RECONCILE_ENABLED=false)")
        return

    hour = getattr(settings, "ch_reconcile_run_hour_utc", RECONCILE_DEFAULT_HOUR_UTC)
    lookback = int(getattr(settings, "ch_reconcile_lookback_days", 7))
    logger.info(
        "ch_reconcile: loop started (CH_RECONCILE_RUN_HOUR_UTC=%s, lookback=%dd; "
        "equities + futures)",
        hour, lookback,
    )
    while True:
        try:
            wait_s = _seconds_until_next_run(hour)
            logger.info("ch_reconcile: sleeping %.0fs until next run", wait_s)
            await asyncio.sleep(wait_s)
            await asyncio.to_thread(reconcile_ch_from_schwab, lookback)
            await asyncio.to_thread(reconcile_ch_from_futures, lookback)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 — keep the loop alive
            logger.exception("ch_reconcile: loop error: %s", exc)
            await asyncio.sleep(300)
