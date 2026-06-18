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


# ── latest_close_per_symbol routes mixed symbols to both tables ──────

def test_latest_close_per_symbol_splits_by_asset_class(monkeypatch):
    from app.db import queries

    class _Res:
        result_rows = []

    captured: list[tuple[str, dict]] = []

    client = MagicMock()
    client.query.side_effect = lambda sql, parameters=None: (
        captured.append((sql, parameters)) or _Res()
    )
    monkeypatch.setattr("app.db.queries.get_client", lambda: client)

    queries.latest_close_per_symbol(["AAPL", "/ES", "MSFT", "/NQ"])

    # One query per asset class, each against its own table with only its syms.
    by_table = {
        ("futures_ohlcv_1m" if "FROM futures_ohlcv_1m" in sql else "ohlcv_1m"): params["syms"]
        for sql, params in captured
    }
    assert by_table["ohlcv_1m"] == ["AAPL", "MSFT"]
    assert by_table["futures_ohlcv_1m"] == ["/ES", "/NQ"]


# ── futures_universe bootstrap ───────────────────────────────────────

def test_bootstrap_futures_seeds_when_empty(monkeypatch):
    from app.services.futures.schemas import FUTURES_SEED_ROOTS
    from app.services.stream.service import StreamService

    svc = StreamService()
    monkeypatch.setattr(svc, "_read_futures_universe", lambda *, owner_id=None: set())
    client = MagicMock()
    monkeypatch.setattr("app.services.stream.service.get_client", lambda: client)

    did, count = svc.bootstrap_futures_if_empty()

    assert did is True
    assert count == len(FUTURES_SEED_ROOTS)
    args, kwargs = client.insert.call_args
    assert args[0] == "futures_universe"
    seeded = [row[0] for row in args[1]]
    assert seeded == list(FUTURES_SEED_ROOTS)


def test_bootstrap_futures_idempotent_when_seeded(monkeypatch):
    from app.services.stream.service import StreamService

    svc = StreamService()
    monkeypatch.setattr(svc, "_read_futures_universe", lambda *, owner_id=None: {"/ES", "/NQ"})
    client = MagicMock()
    monkeypatch.setattr("app.services.stream.service.get_client", lambda: client)

    did, count = svc.bootstrap_futures_if_empty()

    assert did is False
    assert count == 2
    client.insert.assert_not_called()


# ── add / remove futures + descriptions ──────────────────────────────

def test_normalize_futures_symbol():
    from app.services.stream.service import _normalize_futures_symbol

    assert _normalize_futures_symbol("es") == "/ES"
    assert _normalize_futures_symbol("/ES") == "/ES"
    assert _normalize_futures_symbol("  /mnq ") == "/MNQ"
    assert _normalize_futures_symbol("") == ""


def test_futures_root_description():
    from app.services.futures.schemas import futures_root_description

    assert futures_root_description("/ES") == "E-mini S&P 500"
    assert futures_root_description("/MNQ") == "Micro E-mini Nasdaq-100"
    assert futures_root_description("es") == ""  # needs the leading slash
    assert futures_root_description("/UNKNOWN") == ""


def test_futures_catalog_complete_and_sorted():
    from app.services.futures.schemas import (
        FUTURES_ROOT_DESCRIPTIONS,
        FUTURES_SEED_ROOTS,
        futures_catalog,
    )

    cat = futures_catalog()
    syms = [c["symbol"] for c in cat]
    assert len(cat) == len(FUTURES_ROOT_DESCRIPTIONS)
    assert syms == sorted(syms)
    # Every streamed seed root must be discoverable + described.
    for root in FUTURES_SEED_ROOTS:
        assert root in syms
    assert all(c["description"] for c in cat)


def test_add_futures_normalizes_writes_and_subscribes(monkeypatch):
    from app.services.stream.service import StreamService

    svc = StreamService()
    monkeypatch.setattr(svc, "_is_futures_active", lambda sym, *, owner_id=None: False)
    monkeypatch.setattr(svc, "_apply_subscription_diff", lambda before, after: None)
    monkeypatch.setattr(svc, "list_futures_universe", lambda *, owner_id=None: [{"symbol": "/CL"}])
    written: dict = {}
    monkeypatch.setattr(
        svc, "_write_futures_row",
        lambda sym, owner, is_active, **kw: written.update(symbol=sym, is_active=is_active),
    )

    res = svc.add_futures("cl")

    assert res["operation"] == "add"
    assert res["changed"] == ["/CL"]
    assert written == {"symbol": "/CL", "is_active": 1}
    assert "/CL" in svc._subscribed


def test_remove_futures_marks_inactive_and_unsubscribes(monkeypatch):
    from app.services.stream.service import StreamService

    svc = StreamService()
    svc._subscribed = {"/CL"}
    monkeypatch.setattr(svc, "_is_futures_active", lambda sym, *, owner_id=None: True)
    monkeypatch.setattr(svc, "_apply_subscription_diff", lambda before, after: None)
    monkeypatch.setattr(svc, "list_futures_universe", lambda *, owner_id=None: [])
    written: dict = {}
    monkeypatch.setattr(
        svc, "_write_futures_row",
        lambda sym, owner, is_active, **kw: written.update(symbol=sym, is_active=is_active),
    )

    res = svc.remove_futures("/CL")

    assert res["changed"] == ["/CL"]
    assert written == {"symbol": "/CL", "is_active": 0}
    assert "/CL" not in svc._subscribed
