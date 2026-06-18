"""On-demand lake → ClickHouse fill for futures chart requests.

The futures peer of ``app/services/equities/lake_to_ch_fill.py``. When the
bars gateway sees insufficient ``stocks.futures_ohlcv_1m`` coverage for a
root's requested window, it fills that bounded window from
``futures.schwab_futures`` (the authoritative lake) and re-queries CH.

Simpler than the equities path: the futures lake is a SINGLE small,
month-partitioned table (no polygon∪schwab union, no merge-on-read delete
files, no adjustment), so a bounded PyIceberg scan with a (symbol, window)
row-filter is already fast — no Athena round-trip needed.

``_scan_futures_lake`` is shared by the fill path AND the lake-only read
path in the bars gateway (``source='lake'``), so both surfaces read the
exact same bytes.

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


def _scan_futures_lake(
    symbol: str, start: datetime, end: datetime
) -> Optional[pa.Table]:
    """Read ``futures.schwab_futures`` for ``symbol`` in ``[start, end)``.

    Returns a deduped (merge-on-read) Arrow table, or ``None`` on a lake
    read error (logged — NO silent failure; the caller degrades to "serve
    what CH has"). Empty window → 0-row table (not None)."""
    from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, LessThan

    from app.services.futures.tables import ensure_schwab_futures

    if start.tzinfo is None:
        start = start.replace(tzinfo=timezone.utc)
    if end.tzinfo is None:
        end = end.replace(tzinfo=timezone.utc)

    try:
        table = ensure_schwab_futures()
        return table.scan(
            row_filter=And(
                EqualTo("symbol", symbol),
                And(
                    GreaterThanOrEqual("timestamp", start.isoformat()),
                    LessThan("timestamp", end.isoformat()),
                ),
            ),
            selected_fields=(
                "symbol", "timestamp", "open", "high", "low", "close",
                "volume", "vwap", "trade_count",
            ),
        ).to_arrow()
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.error(
            "futures lake scan failed: %s [%s, %s): %s", symbol, start, end, exc,
        )
        return None


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
