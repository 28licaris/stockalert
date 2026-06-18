"""Unit tests for the futures read surface (F4).

A ``/``-prefixed symbol must route the WHOLE bars surface — CH table,
lake fill, lake-only read — to the futures tables, while equities keep
hitting ``ohlcv_1m`` / ``polygon_adjusted``. Mocks keep the suite offline.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pyarrow as pa
import pytest
from unittest.mock import MagicMock

from app.services.futures.symbols import ch_table_for, is_futures_symbol
from app.services.readers.bars_gateway import (
    BarSource,
    _lake_fill_fn,
    get_chart_bars,
    get_range_bars,
)
from app.services.readers.schemas import LiveBar


# ── symbol routing ───────────────────────────────────────────────────

def test_is_futures_symbol():
    assert is_futures_symbol("/ES")
    assert is_futures_symbol("/MNQ")
    assert not is_futures_symbol("AAPL")
    assert not is_futures_symbol("")


def test_ch_table_for():
    assert ch_table_for("/ES") == "futures_ohlcv_1m"
    assert ch_table_for("AAPL") == "ohlcv_1m"


def test_lake_fill_fn_selects_by_asset_class():
    from app.services.equities.lake_to_ch_fill import fill_ch_from_lake_sync
    from app.services.futures.lake_to_ch_fill import fill_ch_from_futures_lake_sync

    assert _lake_fill_fn("/ES") is fill_ch_from_futures_lake_sync
    assert _lake_fill_fn("AAPL") is fill_ch_from_lake_sync


# ── gateway threads the asset-class table into the reader ────────────

class _RecorderReader:
    """Records the source_table each gateway call threads through."""

    def __init__(self, bars):
        self.calls: list[tuple] = []
        self._bars = bars

    def get_bars_for_chart(self, symbol, *, interval="1m", lookback_days=None,
                           limit=None, source_table="ohlcv_1m"):
        self.calls.append(("chart", symbol, source_table))
        return self._bars

    def get_bars_in_range(self, symbol, start, end, *, interval="1m",
                          limit=100_000, source_table="ohlcv_1m"):
        self.calls.append(("range", symbol, source_table))
        return self._bars


def _recent_bar() -> LiveBar:
    # Recent timestamp so _ch_lacks_window() is False → no lake fill.
    return LiveBar(
        symbol="X", timestamp=datetime.now(timezone.utc) - timedelta(hours=1),
        open=1.0, high=1.0, low=1.0, close=1.0, volume=1.0,
        vwap=None, trade_count=None, source="t", interval="1m",
    )


def test_get_chart_bars_routes_futures_to_futures_table():
    r = _RecorderReader([_recent_bar()])
    get_chart_bars("/ES", lookback_days=5, source=BarSource.CLICKHOUSE, reader=r)
    get_chart_bars("AAPL", lookback_days=5, source=BarSource.CLICKHOUSE, reader=r)
    assert r.calls[0] == ("chart", "/ES", "futures_ohlcv_1m")
    assert r.calls[1] == ("chart", "AAPL", "ohlcv_1m")


def test_get_range_bars_routes_futures_to_futures_table():
    r = _RecorderReader([_recent_bar()])
    start = datetime(2026, 6, 16, tzinfo=timezone.utc)
    end = datetime(2026, 6, 17, tzinfo=timezone.utc)
    get_range_bars("/MNQ", start, end, source=BarSource.CLICKHOUSE, reader=r)
    get_range_bars("TSLA", start, end, source=BarSource.CLICKHOUSE, reader=r)
    assert r.calls[0] == ("range", "/MNQ", "futures_ohlcv_1m")
    assert r.calls[1] == ("range", "TSLA", "ohlcv_1m")


# ── futures lake fill targets the futures CH table ───────────────────

def _futures_lake_arrow() -> pa.Table:
    import pandas as pd
    return pa.table({
        "symbol": ["/ES", "/ES"],
        "timestamp": pd.to_datetime(
            ["2026-06-16T14:30:00Z", "2026-06-16T14:31:00Z"]
        ),
        "open": [7559.5, 7559.75],
        "high": [7560.0, 7560.25],
        "low": [7559.0, 7559.5],
        "close": [7559.75, 7560.0],
        "volume": [1200.0, 90.0],
        "vwap": [None, None],
        "trade_count": [None, None],
    })


def test_futures_fill_inserts_into_futures_ch_table(monkeypatch):
    import app.services.futures.lake_to_ch_fill as mod

    monkeypatch.setattr(mod, "_scan_futures_lake", lambda s, a, b: _futures_lake_arrow())
    client = MagicMock()
    monkeypatch.setattr(mod, "get_client", lambda: client)

    n = mod.fill_ch_from_futures_lake_sync(
        "/ES", datetime(2026, 6, 16, tzinfo=timezone.utc),
        datetime(2026, 6, 17, tzinfo=timezone.utc),
    )

    assert n == 2
    args, kwargs = client.insert.call_args
    assert args[0] == "stocks.futures_ohlcv_1m"
    assert kwargs["column_names"][-2:] == ["source", "version"]
    assert all(row[-2] == "lake-fill-futures" for row in args[1])


def test_futures_fill_empty_lake_is_noop(monkeypatch):
    import app.services.futures.lake_to_ch_fill as mod

    monkeypatch.setattr(mod, "_scan_futures_lake", lambda s, a, b: _futures_lake_arrow().slice(0, 0))
    client = MagicMock()
    monkeypatch.setattr(mod, "get_client", lambda: client)

    assert mod.fill_ch_from_futures_lake_sync(
        "/ES", datetime(2026, 6, 16, tzinfo=timezone.utc),
        datetime(2026, 6, 17, tzinfo=timezone.utc),
    ) == 0
    client.insert.assert_not_called()


# ── queries source_table whitelist ───────────────────────────────────

def test_queries_whitelist_allows_futures_rejects_unknown():
    from app.db import queries

    # Futures table is allowed.
    sql = queries._dedup_ohlc_intraday_subquery("futures_ohlcv_1m", "1=1")
    assert "futures_ohlcv_1m" in sql
    # Arbitrary table names are rejected (SQL-injection guard).
    with pytest.raises(ValueError):
        queries._dedup_ohlc_intraday_subquery("evil; DROP TABLE", "1=1")
