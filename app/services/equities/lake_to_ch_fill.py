"""On-demand lake → ClickHouse fill for chart requests.

When `/api/v1/bars` sees insufficient CH coverage for a symbol's
requested window, it calls `fill_ch_from_lake()` to read the bounded
window from `equities.polygon_adjusted` and insert it into
`stocks.ohlcv_1m`. Subsequent requests for that symbol hit CH directly
(sub-100ms), restoring the hot-path latency.

**Scoped to the requested window** so the PyIceberg scan stays fast
(seconds, not minutes). The overnight `hotload_ch_from_lake.py`
handles deep history for the streaming universe; this module fills
gaps on demand — chiefly for:

  - Ad-hoc symbols outside the 206 streaming universe
  - Universe symbols whose hot-load hasn't completed yet
  - Date ranges beyond the current CH coverage

**Concurrency.** Per-symbol asyncio locks dedupe in-flight fills so
N concurrent chart requests for the same symbol only trigger ONE
PyIceberg scan + CH insert. Subsequent requests park on the lock and
return after the first one completes.

**Source tag.** Rows get `source = "lake-fill"` to distinguish from
`silver-polygon` (hotload-loaded), `schwab-stream` (live), and
`schwab-tipfill` (recent-gap fill). ReplacingMergeTree dedupes by
(symbol, timestamp); the version column resolves overlap.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from threading import Lock
from typing import Optional

import pyarrow as pa
from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, LessThan

from app.db.client import get_client
from app.services.equities.schemas import equities_table_id
from app.services.iceberg_catalog import get_catalog

logger = logging.getLogger(__name__)


# Per-symbol asyncio.Lock cache to dedupe concurrent fills. The outer
# threading.Lock protects the dict itself (set on first request per
# symbol). The inner asyncio.Lock serializes the actual fill.
_locks_mu = Lock()
_per_symbol_locks: dict[str, asyncio.Lock] = {}


_CH_COLUMNS = [
    "symbol", "timestamp", "open", "high", "low", "close",
    "volume", "vwap", "trade_count", "source", "version",
]


def _get_lock(symbol: str) -> asyncio.Lock:
    with _locks_mu:
        lock = _per_symbol_locks.get(symbol)
        if lock is None:
            lock = asyncio.Lock()
            _per_symbol_locks[symbol] = lock
        return lock


# Sync per-symbol locks — for callers outside an event loop (the MCP
# tools run in FastMCP's thread pool; the bars gateway is sync). Distinct
# from the asyncio locks above: a threading.Lock can't be awaited and an
# asyncio.Lock can't be held across a plain `with` in a worker thread.
_sync_per_symbol_locks: dict[str, Lock] = {}


def _get_sync_lock(symbol: str) -> Lock:
    with _locks_mu:
        lock = _sync_per_symbol_locks.get(symbol)
        if lock is None:
            lock = Lock()
            _sync_per_symbol_locks[symbol] = lock
        return lock


def fill_ch_from_lake_sync(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    source_tag: str = "lake-fill",
) -> int:
    """Synchronous twin of :func:`fill_ch_from_lake`.

    Same bounded-window scan + CH insert, but callable from sync code
    (the bars gateway, MCP tools running in a worker thread). Dedupes
    concurrent same-symbol fills via a threading.Lock. Returns rows
    inserted (0 on empty window / missing symbol, never raises).
    """
    sym = symbol.upper()
    with _get_sync_lock(sym):
        return _fill_sync(sym, start, end, source_tag)


async def fill_ch_from_lake(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    source_tag: str = "lake-fill",
) -> int:
    """Read [start, end) from polygon_adjusted for `symbol`; insert into CH.

    Returns the number of rows inserted. Returns 0 (without raising) if
    the symbol isn't in `polygon_adjusted` or the window contains no
    data — callers treat that as "no historical data available, serve
    what CH has."

    Bounded by `start`/`end` so the PyIceberg scan is fast. A 90-day
    window for AAPL typically completes in 2-5s; the full 20yr scan
    takes 30+ min by comparison.

    Concurrent calls for the same symbol are serialized via per-symbol
    lock — only one Iceberg scan + CH insert happens at a time.
    """
    sym = symbol.upper()
    lock = _get_lock(sym)
    async with lock:
        return await asyncio.to_thread(_fill_sync, sym, start, end, source_tag)


def _fill_sync(symbol: str, start: datetime, end: datetime, source_tag: str) -> int:
    """Synchronous worker — runs in the asyncio thread pool."""
    try:
        catalog = get_catalog()
        table = catalog.load_table(equities_table_id("polygon_adjusted"))
        # Bucket+month partitioning means symbol+timestamp filter prunes
        # to roughly (months_in_window) files for this symbol's bucket.
        arr = table.scan(
            row_filter=And(
                EqualTo("symbol", symbol),
                GreaterThanOrEqual("timestamp", start.isoformat()),
                LessThan("timestamp", end.isoformat()),
            )
        ).to_arrow()
    except Exception as exc:
        logger.warning(
            "lake_fill: %s [%s, %s) iceberg scan failed: %s",
            symbol, start, end, exc,
        )
        return 0

    if arr.num_rows == 0:
        logger.info(
            "lake_fill: %s [%s, %s) -> 0 rows in lake",
            symbol, start, end,
        )
        return 0

    rows = _arrow_to_ch_rows(arr, symbol, source_tag)
    try:
        ch = get_client()
        ch.insert("stocks.ohlcv_1m", rows, column_names=_CH_COLUMNS)
    except Exception as exc:
        logger.error(
            "lake_fill: %s CH insert failed (rows=%d): %s",
            symbol, len(rows), exc,
        )
        return 0

    logger.info(
        "lake_fill: %s [%s, %s) -> %d rows inserted (source=%s)",
        symbol, start, end, len(rows), source_tag,
    )
    return len(rows)


def _arrow_to_ch_rows(arr: pa.Table, symbol: str, source_tag: str) -> list[list]:
    """Convert PyArrow Table → list-of-lists for clickhouse-connect insert.

    Mirrors `_arrow_to_rows` in scripts/hotload_ch_from_lake.py — handles
    the polygon_adjusted quirks (fractional volume → float, fractional
    trade_count → round to int, null vwap → 0.0).
    """
    cols = arr.to_pydict()
    out: list[list] = []
    for i in range(arr.num_rows):
        out.append([
            symbol,
            cols["timestamp"][i],
            float(cols["open"][i]),
            float(cols["high"][i]),
            float(cols["low"][i]),
            float(cols["close"][i]),
            float(cols["volume"][i]) if cols["volume"][i] is not None else 0.0,
            float(cols["vwap"][i]) if cols["vwap"][i] is not None else 0.0,
            int(round(cols["trade_count"][i])) if cols["trade_count"][i] is not None else 0,
            source_tag,
            1,  # ReplacingMergeTree version
        ])
    return out
