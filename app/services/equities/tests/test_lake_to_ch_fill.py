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


# ── bulk (Arrow-native) path ──────────────────────────────────────────────

def _messy_arrow() -> pa.Table:
    """Rows exercising every null/rounding rule of the row converter."""
    ts = [datetime(2026, 6, 1, 14, 30 + i, tzinfo=timezone.utc) for i in range(5)]
    return pa.table(
        {
            "symbol": ["AAA", "AAA", "BBB", "BBB", "BBB"],
            "timestamp": pa.array(ts, pa.timestamp("us", tz="UTC")),
            "open": [1.0, 1.1, 2.0, 2.1, 2.2],
            "high": [2.0, 2.1, 3.0, 3.1, 3.2],
            "low": [0.5, 0.6, 1.5, 1.6, 1.7],
            "close": [1.5, 1.6, 2.5, 2.6, 2.7],
            "volume": [100.0, None, 5.5, 0.0, 7.0],
            "vwap": [1.2, None, None, 2.4, 2.5],
            "trade_count": [5.0, None, 4.5, 5.5, 6.4],  # 4.5 -> 4, 5.5 -> 6 (half-to-even)
            "adj_factor": [1.0] * 5,
            "source": ["polygon-adjusted"] * 5,
        }
    )


def test_arrow_converter_matches_the_row_converter_cell_for_cell():
    arr = _messy_arrow()
    table = fill._arrow_to_ch_table(arr, "lake-rebuild")
    assert table.column_names == fill._CH_COLUMNS
    got = table.to_pylist()
    for i, sym in enumerate(arr.column("symbol").to_pylist()):
        expected = fill._arrow_to_ch_rows(arr.slice(i, 1), sym, "lake-rebuild")[0]
        assert [got[i][c] for c in fill._CH_COLUMNS] == expected, f"row {i} differs"


def test_arrow_converter_types_are_what_clickhouse_expects():
    t = fill._arrow_to_ch_table(_messy_arrow(), "x")
    assert t.schema.field("timestamp").type == pa.timestamp("ms", tz="UTC")
    assert t.schema.field("trade_count").type == pa.int64() and t.schema.field("volume").type == pa.float64()
    assert t.column("source").to_pylist() == ["x"] * 5 and t.column("version").to_pylist() == [1] * 5


class _FakeArrowCH:
    def __init__(self) -> None:
        self.calls: list[tuple[str, int]] = []

    def insert_arrow(self, table, data):  # noqa: ANN001
        self.calls.append((table, data.num_rows))


def test_batch_reads_once_inserts_arrow_and_reports_rows_per_symbol(monkeypatch):
    reads: list = []
    monkeypatch.setattr(read_arrow_mod, "read_arrow", lambda syms, *a, **k: reads.append(syms) or _messy_arrow())
    ch = _FakeArrowCH()
    monkeypatch.setattr(fill, "get_client", lambda: ch)
    t = datetime(2026, 6, 1, tzinfo=timezone.utc)
    out = fill.fill_ch_from_lake_batch_sync(["bbb", "AAA", "CCC"], t, t, source_tag="lake-rebuild")
    assert out == {"AAA": 2, "BBB": 3, "CCC": 0}      # CCC has no data: 0, not missing
    assert reads == [["AAA", "BBB", "CCC"]]            # ONE read for the whole batch
    assert ch.calls == [("stocks.ohlcv_1m", 5)]


def test_batch_splits_large_results_into_bounded_inserts(monkeypatch):
    monkeypatch.setattr(read_arrow_mod, "read_arrow", lambda *a, **k: _messy_arrow())
    monkeypatch.setattr(fill, "_BATCH_INSERT_ROWS", 2)
    ch = _FakeArrowCH()
    monkeypatch.setattr(fill, "get_client", lambda: ch)
    t = datetime(2026, 6, 1, tzinfo=timezone.utc)
    fill.fill_ch_from_lake_batch_sync(["AAA", "BBB"], t, t)
    assert [n for _, n in ch.calls] == [2, 2, 1]


def test_batch_empty_window_never_touches_clickhouse(monkeypatch):
    monkeypatch.setattr(read_arrow_mod, "read_arrow", lambda *a, **k: _arrow(0))
    ch = _FakeArrowCH()
    monkeypatch.setattr(fill, "get_client", lambda: ch)
    t = datetime(2026, 6, 1, tzinfo=timezone.utc)
    assert fill.fill_ch_from_lake_batch_sync(["AAA"], t, t) == {"AAA": 0} and ch.calls == []


def test_batch_raises_so_the_caller_can_fall_back(monkeypatch):
    import pytest

    def boom(*a, **k):
        raise RuntimeError("glue throttled")

    monkeypatch.setattr(read_arrow_mod, "read_arrow", boom)
    t = datetime(2026, 6, 1, tzinfo=timezone.utc)
    with pytest.raises(RuntimeError):
        fill.fill_ch_from_lake_batch_sync(["AAA"], t, t)
