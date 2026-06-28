"""Regression tests for the lake→ClickHouse fill.

Guards the v2 fix: the fill must route through `read_arrow` (the
polygon∪schwab read-time-adjusted union) and never again through the
retired `polygon_adjusted` Athena path (which silently returned 0 rows).
Both dependencies are mocked — no live lake / ClickHouse needed.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pyarrow as pa

import app.services.equities.lake_to_ch_fill as fill
import app.services.readers.read_arrow as read_arrow_mod


def _arrow(rows: int) -> pa.Table:
    ts = [datetime(2026, 6, 1, 14, 30 + i, tzinfo=timezone.utc) for i in range(rows)]
    return pa.table(
        {
            "symbol": ["XYZ"] * rows,
            "timestamp": ts,
            "open": [1.0] * rows,
            "high": [2.0] * rows,
            "low": [0.5] * rows,
            "close": [1.5] * rows,
            "volume": [100.0] * rows,
            "vwap": [1.2] * rows,
            "trade_count": [5] * rows,
            "adj_factor": [1.0] * rows,
            "source": ["polygon-adjusted"] * rows,
        }
    )


class _FakeCH:
    def __init__(self) -> None:
        self.inserted: list = []

    def insert(self, table, rows, column_names=None):  # noqa: ANN001
        self.inserted.append((table, rows, column_names))


def test_fill_routes_through_read_arrow_and_inserts(monkeypatch):
    monkeypatch.setattr(read_arrow_mod, "read_arrow", lambda *a, **k: _arrow(3))
    ch = _FakeCH()
    monkeypatch.setattr(fill, "get_client", lambda: ch)

    t = datetime(2026, 6, 1, tzinfo=timezone.utc)
    n = fill.fill_ch_from_lake_sync("XYZ", t, t)

    assert n == 3
    table, rows, cols = ch.inserted[0]
    assert table == "stocks.ohlcv_1m"
    assert cols == fill._CH_COLUMNS
    assert rows[0][0] == "XYZ"  # symbol column
    assert rows[0][-1] == 1  # ReplacingMergeTree version


def test_fill_empty_window_inserts_nothing(monkeypatch):
    monkeypatch.setattr(read_arrow_mod, "read_arrow", lambda *a, **k: _arrow(0))
    ch = _FakeCH()
    monkeypatch.setattr(fill, "get_client", lambda: ch)

    t = datetime(2026, 6, 1, tzinfo=timezone.utc)
    n = fill.fill_ch_from_lake_sync("XYZ", t, t)

    assert n == 0
    assert ch.inserted == []  # never touches CH on an empty lake window


def test_fill_degrades_to_zero_when_read_arrow_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("glue down")

    monkeypatch.setattr(read_arrow_mod, "read_arrow", boom)
    ch = _FakeCH()
    monkeypatch.setattr(fill, "get_client", lambda: ch)

    t = datetime(2026, 6, 1, tzinfo=timezone.utc)
    n = fill.fill_ch_from_lake_sync("XYZ", t, t)

    assert n == 0  # logged + degraded, never crashes the caller
    assert ch.inserted == []
