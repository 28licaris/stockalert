"""On-demand lake → ClickHouse fill for chart requests.

When `/api/v1/bars` sees insufficient CH coverage for a symbol's
requested window, it calls `fill_ch_from_lake()` to read the bounded
window from the lake via `read_arrow` (the polygon∪schwab read-time-
adjusted union, schwab tip included) and insert it into
`stocks.ohlcv_1m`. Subsequent requests for that symbol hit CH directly
(sub-100ms), restoring the hot-path latency.

**Scoped to the requested window** so the scan stays bounded. The bulk
`scripts/rebuild_ch_from_lake.py` reloads deep history for the whole
streaming universe (it loops THIS same fill path per symbol); this
module fills gaps on demand — chiefly for:

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

from app.db.client import get_client

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
    """Read [start, end) from the lake union (read_arrow) for `symbol`;
    insert into CH.

    Returns the number of rows inserted. Returns 0 (without raising) if
    the symbol isn't in the lake or the window contains no data — callers
    treat that as "no historical data available, serve what CH has."

    Bounded by `start`/`end` so the scan stays per-symbol. The union read
    carries a fixed planning/engine overhead (~30s) that dominates small
    windows; acceptable for one-time onboarding and the async background
    self-heal. (An Athena fast-path is a possible future optimization.)

    Concurrent calls for the same symbol are serialized via per-symbol
    lock — only one Iceberg scan + CH insert happens at a time.
    """
    sym = symbol.upper()
    lock = _get_lock(sym)
    async with lock:
        return await asyncio.to_thread(_fill_sync, sym, start, end, source_tag)


def _fill_sync(symbol: str, start: datetime, end: datetime, source_tag: str) -> int:
    """Synchronous worker — runs in the asyncio thread pool.

    Reads the window from the lake via ``read_arrow`` — the modular cold
    read path that unions ``polygon_adjusted`` (read-time split-adjusted
    from ``polygon_raw`` + ``market_splits``) ∪ ``schwab_universe``,
    polygon winning overlaps, and INCLUDES the recent schwab tip. This
    replaced the Athena UNLOAD path, which still targeted the retired
    materialized ``equities.polygon_adjusted`` table and so silently
    returned zero rows for every fill (the v2 read-time-adjustment
    migration left it behind). See docs/data/lake_read_layer.md.
    """
    from app.services.readers.read_arrow import read_arrow

    try:
        # sources=None → the full union (polygon wins contested (symbol, ts)).
        arr = read_arrow(symbol, start, end)
    except Exception as exc:  # noqa: BLE001 — boundary; degrade, never crash a fill
        logger.error("lake_fill: %s read_arrow failed: %s", symbol, exc)
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


# Rows per ClickHouse insert in the bulk path (one HTTP request each).
_BATCH_INSERT_ROWS = 1_000_000


def _arrow_to_ch_table(arr: pa.Table, source_tag: str) -> pa.Table:
    """Arrow-native twin of ``_arrow_to_ch_rows``: same columns, same null rules,
    no per-row Python. Parity with the row converter is pinned by a test.

    The row converter costs ~10k rows/s (a Python loop with float()/round() per
    cell); a full rebuild of ~150M rows spent hours there while ClickHouse idled.
    """
    import pyarrow.compute as pc

    n = arr.num_rows

    def f64(col: str):
        return pc.cast(arr.column(col), pa.float64())

    return pa.table(
        {
            "symbol": pc.cast(arr.column("symbol"), pa.string()),
            "timestamp": pc.cast(arr.column("timestamp"), pa.timestamp("ms", tz="UTC")),
            "open": f64("open"),
            "high": f64("high"),
            "low": f64("low"),
            "close": f64("close"),
            "volume": pc.fill_null(f64("volume"), 0.0),
            "vwap": pc.fill_null(f64("vwap"), 0.0),
            # Python round() is half-to-even, as is pc.round's default.
            "trade_count": pc.fill_null(pc.cast(pc.round(f64("trade_count")), pa.int64()), 0),
            "source": pa.repeat(source_tag, n),
            "version": pa.repeat(1, n),  # ReplacingMergeTree version
        }
    )


def fill_ch_from_lake_batch_sync(
    symbols: list[str],
    start: datetime,
    end: datetime,
    *,
    source_tag: str = "lake-fill",
) -> dict[str, int]:
    """Bulk variant of ``_fill_sync`` for many symbols.

    ONE lake read (``read_arrow`` accepts a symbol list), so the ~30 s per-call
    planning overhead is paid once per batch instead of once per symbol, then Arrow
    inserts of up to ``_BATCH_INSERT_ROWS`` rows. Returns rows inserted per symbol
    (0 for a symbol with no data in the window).

    Unlike ``_fill_sync`` this RAISES on a lake or insert failure, so a caller
    that needs a result (the bulk rebuild) can fall back per symbol instead of
    silently recording zeros.
    """
    from app.services.readers.read_arrow import read_arrow

    syms = sorted({s.upper() for s in symbols})
    arr = read_arrow(syms, start, end)
    if arr.num_rows == 0:
        logger.info("lake_fill_batch: %d symbols [%s, %s) -> 0 rows in lake", len(syms), start, end)
        return {s: 0 for s in syms}

    table = _arrow_to_ch_table(arr, source_tag)
    ch = get_client()
    for offset in range(0, table.num_rows, _BATCH_INSERT_ROWS):
        ch.insert_arrow("stocks.ohlcv_1m", table.slice(offset, _BATCH_INSERT_ROWS))

    import pyarrow.compute as pc

    counts = {r["values"]: r["counts"] for r in pc.value_counts(table.column("symbol")).to_pylist()}
    result = {s: int(counts.get(s, 0)) for s in syms}
    logger.info(
        "lake_fill_batch: %d symbols [%s, %s) -> %d rows inserted (source=%s)",
        len(syms), start, end, table.num_rows, source_tag,
    )
    return result


def _arrow_to_ch_rows(arr: pa.Table, symbol: str, source_tag: str) -> list[list]:
    """Convert PyArrow Table → list-of-lists for clickhouse-connect insert.

    Handles the read_arrow column quirks (fractional volume → float,
    fractional trade_count → round to int, null vwap → 0.0).
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
