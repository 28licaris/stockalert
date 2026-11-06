"""``_scan_futures_lake`` unions the Schwab and polygon-continuous tables.

Regression: the two Iceberg tables return the same column with different Arrow
types (``symbol`` is ``string`` vs ``large_string``), and ``pa.concat_tables``
raised ``ArrowInvalid: Schema at index 1 was different``. That broke the futures
step of ``scripts/ch_bootstrap.py`` and every futures chart read that had rows in
both tiers. No Iceberg or ClickHouse: the table scans are stubbed.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pytest

from app.services.futures import lake_to_ch_fill as mod

T0 = datetime(2026, 5, 20, 14, 30, tzinfo=timezone.utc)


def _table(symbol_type: pa.DataType, minutes: list[int], close: float, symbol: str = "/ES") -> pa.Table:
    schema = pa.schema(
        [
            pa.field("symbol", symbol_type, nullable=False),
            pa.field("timestamp", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("open", pa.float64()),
            pa.field("high", pa.float64()),
            pa.field("low", pa.float64()),
            pa.field("close", pa.float64()),
            pa.field("volume", pa.float64()),
            pa.field("vwap", pa.float64()),
            pa.field("trade_count", pa.int64()),
        ]
    )
    n = len(minutes)
    return pa.table(
        {
            "symbol": pa.array([symbol] * n, type=symbol_type),
            "timestamp": pa.array([T0 + timedelta(minutes=m) for m in minutes], type=pa.timestamp("us", tz="UTC")),
            "open": [close] * n, "high": [close] * n, "low": [close] * n, "close": [close] * n,
            "volume": [1.0] * n, "vwap": [close] * n, "trade_count": [1] * n,
        },
        schema=schema,
    )


@pytest.fixture
def stub_tiers(monkeypatch):
    """Make `_scan_futures_lake` see the given (polygon, schwab) tables."""
    from app.services.futures import tables

    def install(poly: pa.Table, schwab: pa.Table) -> None:
        monkeypatch.setattr(tables, "ensure_schwab_futures", lambda: "schwab")
        monkeypatch.setattr(tables, "ensure_polygon_continuous", lambda: "poly")
        monkeypatch.setattr(
            mod, "_scan_one", lambda table, symbol, start, end: schwab if table == "schwab" else poly
        )

    return install


def _scan():
    return mod._scan_futures_lake("/ES", T0 - timedelta(days=1), T0 + timedelta(days=1))


def test_union_with_string_vs_large_string_symbol_does_not_raise(stub_tiers):
    poly = _table(pa.string(), [0, 1, 2, 3], close=100.0)
    schwab = _table(pa.large_string(), [2, 3, 4], close=200.0)
    stub_tiers(poly, schwab)

    merged = _scan()

    assert merged.num_rows == 5  # minutes 0..4, deduplicated
    minutes = [(t - T0).total_seconds() / 60 for t in merged.column("timestamp").to_pylist()]
    assert minutes == [0, 1, 2, 3, 4]  # ascending
    closes = dict(zip(minutes, merged.column("close").to_pylist()))
    assert closes[0] == closes[1] == 100.0          # polygon-only minutes
    assert closes[2] == closes[3] == closes[4] == 200.0  # Schwab wins on ties


def test_union_with_identical_types_still_works(stub_tiers):
    stub_tiers(_table(pa.large_string(), [0, 1], 100.0), _table(pa.large_string(), [1, 2], 200.0))
    assert _scan().num_rows == 3


def test_one_tier_empty_returns_the_other_unchanged(stub_tiers):
    schwab = _table(pa.large_string(), [0, 1], 200.0)
    stub_tiers(_table(pa.string(), [], 100.0), schwab)
    assert _scan().num_rows == 2


def test_fill_reports_rows_instead_of_raising(stub_tiers, monkeypatch):
    """The fill's contract is 'never raises'; the mixed-type union used to violate it."""
    stub_tiers(_table(pa.string(), [0, 1], 100.0), _table(pa.large_string(), [1, 2], 200.0))
    inserted: list[int] = []

    class _Client:
        def insert(self, table, rows, column_names=None):
            inserted.append(len(rows))

    monkeypatch.setattr(mod, "get_client", lambda: _Client())

    n = mod.fill_ch_from_futures_lake_sync("/ES", T0 - timedelta(days=1), T0 + timedelta(days=1))

    assert n == 3 and inserted == [3]
