"""On-demand lake → ClickHouse fill for futures chart requests.

The futures peer of ``app/services/equities/lake_to_ch_fill.py``. When the
bars gateway detects insufficient ``stocks.futures_ohlcv_1m`` coverage for a
root's requested window, it fills that bounded window from the futures lake
and re-queries CH.

Lake sources (unioned at read time):
  • ``futures.schwab_futures``  — recent ~48-day 1-min from Schwab
  • ``futures.polygon_futures`` — deep 1-min history from Polygon backfill

``_scan_futures_lake`` unions both tables, deduplicating by timestamp (Schwab
wins on ties — it's live-sourced). The caller doesn't need to know which
source has coverage; it just gets the best available bars.

Source tag: ``lake-fill-futures`` distinguishes gateway fills from
``schwab-stream`` (live) and ``lake-reconcile-schwab_futures`` (nightly
self-heal). ReplacingMergeTree dedupes by (symbol, timestamp); version
resolves overlap.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from threading import Lock
from typing import Optional

import pyarrow as pa
import pyarrow.compute

from app.db.client import get_client

logger = logging.getLogger(__name__)

_CH_COLUMNS = [
    "symbol", "timestamp", "open", "high", "low", "close",
    "volume", "vwap", "trade_count", "source", "version",
]

# Per-symbol sync locks dedupe concurrent same-root fills (the gateway +
# MCP tools are sync / run in worker threads).
_locks_mu = Lock()
_sync_per_symbol_locks: dict[str, Lock] = {}


def _get_sync_lock(symbol: str) -> Lock:
    with _locks_mu:
        lock = _sync_per_symbol_locks.get(symbol)
        if lock is None:
            lock = Lock()
            _sync_per_symbol_locks[symbol] = lock
        return lock


_LAKE_SELECTED_FIELDS = (
    "symbol", "timestamp", "open", "high", "low", "close",
    "volume", "vwap", "trade_count",
)


def _scan_one(table, symbol: str, start: datetime, end: datetime) -> Optional[pa.Table]:
    """Scan a single Iceberg table for symbol in [start, end).
    Returns None on read error, 0-row table on empty window."""
    from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, LessThan

    try:
        return table.scan(
            row_filter=And(
                EqualTo("symbol", symbol),
                And(
                    GreaterThanOrEqual("timestamp", start.isoformat()),
                    LessThan("timestamp", end.isoformat()),
                ),
            ),
            selected_fields=_LAKE_SELECTED_FIELDS,
        ).to_arrow()
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.error("futures lake scan failed on %s for %s [%s, %s): %s",
                     table.name(), symbol, start, end, exc)
        return None


def _scan_futures_lake(
    symbol: str, start: datetime, end: datetime
) -> Optional[pa.Table]:
    """Read futures lake tables for ``symbol`` in ``[start, end)``.

    Unions ``futures.schwab_futures`` (recent, ~48d) and
    ``futures.polygon_futures`` (deep history) so CH fills draw from
    whichever tier has coverage. Deduplicates by timestamp: when both
    tables have a bar for the same minute, the Schwab bar is kept
    (it's live-sourced and more recent).

    Returns a merged Arrow table (possibly 0 rows), or ``None`` only
    when BOTH scans fail (logged — callers degrade to "serve what CH has").
    """
    from pyiceberg.exceptions import NoSuchTableError

    from app.services.futures.tables import ensure_polygon_futures, ensure_schwab_futures

    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    # Schwab table — authoritative for recent data.
    schwab_arr: Optional[pa.Table] = None
    try:
        schwab_arr = _scan_one(ensure_schwab_futures(), symbol, start, end)
    except Exception as exc:
        logger.error("futures lake: schwab scan error for %s: %s", symbol, exc)

    # Polygon table — deep history; may not exist yet.
    poly_arr: Optional[pa.Table] = None
    try:
        poly_arr = _scan_one(ensure_polygon_futures(), symbol, start, end)
    except NoSuchTableError:
        pass  # polygon_futures not yet created — normal before first backfill
    except Exception as exc:
        logger.warning("futures lake: polygon scan error for %s: %s", symbol, exc)

    # Both failed — propagate None so the caller can degrade gracefully.
    if schwab_arr is None and poly_arr is None:
        return None

    # Only one source available — return it directly.
    if schwab_arr is None or schwab_arr.num_rows == 0:
        return poly_arr
    if poly_arr is None or poly_arr.num_rows == 0:
        return schwab_arr

    # Both have rows — union, deduplicate by timestamp keeping Schwab wins.
    # Sort combined table by timestamp; for ties the Schwab rows come second
    # (appended last) and we keep_first=False so the LAST occurrence (Schwab)
    # is retained after dedup. Then sort ascending for the CH insert.
    combined = pa.concat_tables([poly_arr, schwab_arr])
    ts_col = combined.column("timestamp")
    sort_idx = pa.compute.sort_indices(ts_col)
    sorted_tbl = combined.take(sort_idx)

    # Deduplicate by timestamp: keep the LAST occurrence (Schwab wins on ties).
    timestamps = sorted_tbl.column("timestamp").to_pylist()
    seen: set = set()
    keep: list[int] = []
    for i in range(len(timestamps) - 1, -1, -1):
        ts = timestamps[i]
        if ts not in seen:
            seen.add(ts)
            keep.append(i)
    keep.reverse()
    deduped = sorted_tbl.take(keep)

    logger.debug(
        "futures lake union: %s schwab=%d poly=%d merged=%d",
        symbol, schwab_arr.num_rows, poly_arr.num_rows, deduped.num_rows,
    )
    return deduped


def fill_ch_from_futures_lake_sync(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    source_tag: str = "lake-fill-futures",
) -> int:
    """Scan the futures lake for ``[start, end)`` and insert into
    ``stocks.futures_ohlcv_1m``. Returns rows inserted (0 on empty
    window / missing root / lake error; never raises). Concurrent
    same-root fills are serialized via a per-symbol lock."""
    sym = symbol.upper()
    with _get_sync_lock(sym):
        arr = _scan_futures_lake(sym, start, end)
        if arr is None:
            return 0
        if arr.num_rows == 0:
            logger.info("futures lake_fill: %s [%s, %s) -> 0 rows in lake", sym, start, end)
            return 0

        rows = _arrow_to_ch_rows(arr, source_tag)
        try:
            get_client().insert("stocks.futures_ohlcv_1m", rows, column_names=_CH_COLUMNS)
        except Exception as exc:  # noqa: BLE001 — boundary
            logger.error("futures lake_fill: %s CH insert failed (rows=%d): %s", sym, len(rows), exc)
            return 0

        logger.info(
            "futures lake_fill: %s [%s, %s) -> %d rows inserted (source=%s)",
            sym, start, end, len(rows), source_tag,
        )
        return len(rows)


def _arrow_to_ch_rows(arr: pa.Table, source_tag: str) -> list[list]:
    """Futures lake Arrow → futures_ohlcv_1m row list (version=1; the
    ReplacingMergeTree resolves overlap with live/reconcile bars)."""
    cols = arr.to_pydict()
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
            source_tag,
            1,
        ])
    return out
