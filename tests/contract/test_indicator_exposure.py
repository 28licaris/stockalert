"""
TA-3.2 — Indicator exposure layer: unit + route + MCP + cross-consumer
consistency.

What this covers:

  1. **IndicatorReader unit tests** — with a stubbed bar source so the
     tests don't need CH or AWS. Single-output and multi-output
     indicators decompose correctly. Empty bar sources degrade
     gracefully. Unknown indicators raise.

  2. **Route tests** — `GET /api/indicators/series` +
     `POST /api/indicators/chart-data` via TestClient with the reader
     dependency overridden by a stub.

  3. **MCP tool tests** — `compute_indicator` + `compute_indicators` +
     `get_chart_data` via `mcp.call_tool` with the same stub.

  4. **Cross-consumer consistency gate** — same indicator + same bars
     queried through the route AND the MCP tool produces byte-identical
     values. Locks in the "single source of truth" property.
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import patch

import pandas as pd
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.routes_indicators import get_indicator_reader as _route_reader_dep
from app.api.routes_indicators import router as indicators_router
from app.mcp.server import mcp, register_all_tools
from app.services.readers.indicator_reader import (
    IndicatorReader,
    _bars_to_df,
    _format_label,
    _pd_series_to_indicator_values,
)
from app.services.readers.schemas import (
    BronzeBar,
    IndicatorChartData,
    IndicatorSeries,
    IndicatorValue,
)


register_all_tools()


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _bar(symbol: str, day: int, close: float) -> BronzeBar:
    return BronzeBar(
        symbol=symbol,
        timestamp=datetime(2024, 8, day, tzinfo=timezone.utc),
        open=close - 0.5,
        high=close + 0.5,
        low=close - 0.7,
        close=close,
        volume=1000.0 + day,
        source="test",
    )


def _bars(symbol: str, closes: list[float]) -> list[BronzeBar]:
    return [_bar(symbol, i + 1, c) for i, c in enumerate(closes)]


def _stub_reader(bars: list[BronzeBar], snapshot_id: str | None = "test-snap") -> IndicatorReader:
    """An IndicatorReader whose `_fetch_bars` is patched to return `bars`."""
    reader = IndicatorReader()
    reader._fetch_bars = lambda symbol, start, end, interval: (bars, snapshot_id)  # type: ignore[method-assign]
    return reader


def _unwrap(result: Any) -> Any:
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    if isinstance(result, dict):
        return result
    if isinstance(result, (list, tuple)) and result:
        text = getattr(result[0], "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"unexpected call_tool result: {result!r}")


# ─────────────────────────────────────────────────────────────────────
# Helpers — _bars_to_df, _pd_series_to_indicator_values, _format_label
# ─────────────────────────────────────────────────────────────────────


def test_bars_to_df_indexed_by_timestamp() -> None:
    bars = _bars("TEST", [100.0, 101.0, 102.0])
    df = _bars_to_df(bars)
    assert df.index.name == "timestamp"
    assert list(df.columns) == ["open", "high", "low", "close", "volume"]
    assert list(df["close"]) == [100.0, 101.0, 102.0]


def test_bars_to_df_empty_returns_empty_df() -> None:
    df = _bars_to_df([])
    assert df.empty


def test_pd_series_to_indicator_values_nan_to_none() -> None:
    idx = pd.DatetimeIndex([
        datetime(2024, 8, 1, tzinfo=timezone.utc),
        datetime(2024, 8, 2, tzinfo=timezone.utc),
    ])
    series = pd.Series([float("nan"), 42.0], index=idx)
    values = _pd_series_to_indicator_values(idx, series)
    assert values[0].value is None
    assert values[1].value == 42.0


def test_pd_series_to_indicator_values_reindexes_when_lengths_differ() -> None:
    """ATR-style: series shorter than index → reindex fills with NaN → None."""
    idx = pd.DatetimeIndex([
        datetime(2024, 8, 1, tzinfo=timezone.utc),
        datetime(2024, 8, 2, tzinfo=timezone.utc),
        datetime(2024, 8, 3, tzinfo=timezone.utc),
    ])
    short_series = pd.Series([10.0], index=idx[2:])
    values = _pd_series_to_indicator_values(idx, short_series)
    assert values[0].value is None
    assert values[1].value is None
    assert values[2].value == 10.0


def test_format_label_default() -> None:
    assert _format_label("sma", {"period": 20}) == "SMA(20)"
    assert _format_label("rsi", {"period": 14}) == "RSI(14)"
    assert _format_label("bollinger", {"period": 20, "std_multiplier": 2.0}) == "BB(20, 2.0)"
    # With bare component name
    assert _format_label("bollinger", {"period": 20, "std_multiplier": 2.0}, component="upper") == "BB Upper(20, 2.0)"
    # Multi-word component
    assert _format_label("bollinger", {"period": 20}, component="percent_b") == "BB Percent B(20)"
    # Component pre-prefixed with indicator name (as decomposed by
    # _expand_indicator) — prefix stripped so the label doesn't say
    # "BB Bollinger Upper".
    assert _format_label("bollinger", {"period": 20}, component="bollinger_upper") == "BB Upper(20)"
    assert _format_label("bollinger", {"period": 20}, component="bollinger_percent_b") == "BB Percent B(20)"


# ─────────────────────────────────────────────────────────────────────
# IndicatorReader — single-indicator
# ─────────────────────────────────────────────────────────────────────


def test_get_series_sma_basic() -> None:
    bars = _bars("TEST", [10.0, 12.0, 14.0, 16.0, 18.0])
    reader = _stub_reader(bars)
    series = reader.get_series(
        "TEST", "sma", {"period": 3},
        datetime(2024, 8, 1, tzinfo=timezone.utc),
        datetime(2024, 8, 6, tzinfo=timezone.utc),
        interval="1d",
    )
    assert series.name == "sma"
    assert series.params == {"period": 3}
    assert series.label == "SMA(3)"
    assert series.count == 5
    # First 2 values NaN -> None; values from index 2 onwards.
    assert series.values[0].value is None
    assert series.values[1].value is None
    assert series.values[2].value == pytest.approx(12.0)  # (10+12+14)/3
    assert series.values[4].value == pytest.approx(16.0)  # (14+16+18)/3


def test_get_series_empty_bars_returns_empty_series() -> None:
    reader = _stub_reader(bars=[])
    series = reader.get_series(
        "TEST", "sma", {"period": 3},
        datetime(2024, 8, 1, tzinfo=timezone.utc),
        datetime(2024, 8, 6, tzinfo=timezone.utc),
    )
    assert series.count == 0
    assert series.values == []
    assert series.name == "sma"
    assert series.label == "SMA(3)"


def test_get_series_bollinger_returns_canonical_middle_only() -> None:
    """Single-call route returns only the canonical output for multi-output indicators."""
    bars = _bars("TEST", [float(c) for c in range(1, 25)])
    reader = _stub_reader(bars)
    series = reader.get_series(
        "TEST", "bollinger", {"period": 10},
        datetime(2024, 8, 1, tzinfo=timezone.utc),
        datetime(2024, 8, 24, tzinfo=timezone.utc),
    )
    # Should be exactly one series (the middle band — SMA(10)), not 5.
    assert series.name == "bollinger"
    # Past warmup: middle = SMA(10).
    assert series.values[9].value == pytest.approx(5.5)  # (1..10)/10


def test_get_series_unknown_indicator_raises() -> None:
    reader = _stub_reader(_bars("TEST", [10.0, 11.0]))
    with pytest.raises(ValueError, match="Unknown indicator"):
        reader.get_series(
            "TEST", "supertrend", {},
            datetime(2024, 8, 1, tzinfo=timezone.utc),
            datetime(2024, 8, 3, tzinfo=timezone.utc),
        )


# ─────────────────────────────────────────────────────────────────────
# IndicatorReader — get_chart_data + multi-output decomposition
# ─────────────────────────────────────────────────────────────────────


def test_get_chart_data_multi_indicator_single_output() -> None:
    bars = _bars("TEST", [10.0, 12.0, 14.0, 16.0, 18.0])
    reader = _stub_reader(bars)
    chart = reader.get_chart_data(
        "TEST",
        indicator_specs=[
            {"name": "sma", "params": {"period": 3}},
            {"name": "ema", "params": {"period": 3}},
        ],
        start=datetime(2024, 8, 1, tzinfo=timezone.utc),
        end=datetime(2024, 8, 6, tzinfo=timezone.utc),
    )
    assert chart.symbol == "TEST"
    assert len(chart.bars) == 5
    assert len(chart.series) == 2
    names = {s.name for s in chart.series}
    assert names == {"sma", "ema"}
    assert chart.snapshot_id == "test-snap"


def test_get_chart_data_bollinger_decomposes_to_five_series() -> None:
    """Bollinger spec expands into 5 IndicatorSeries entries."""
    bars = _bars("TEST", [float(c) for c in range(1, 25)])
    reader = _stub_reader(bars)
    chart = reader.get_chart_data(
        "TEST",
        indicator_specs=[
            {"name": "bollinger", "params": {"period": 10, "std_multiplier": 2.0}},
        ],
        start=datetime(2024, 8, 1, tzinfo=timezone.utc),
        end=datetime(2024, 8, 24, tzinfo=timezone.utc),
    )
    names = {s.name for s in chart.series}
    assert names == {
        "bollinger_upper", "bollinger_middle", "bollinger_lower",
        "bollinger_bandwidth", "bollinger_percent_b",
    }
    # Labels include the component name.
    by_name = {s.name: s for s in chart.series}
    assert "Upper" in by_name["bollinger_upper"].label
    assert "Middle" in by_name["bollinger_middle"].label


def test_get_chart_data_stochastic_decomposes_to_two_series() -> None:
    bars = []
    for i in range(20):
        bars.append(_bar("TEST", i + 1, 100.0 + i))
    reader = _stub_reader(bars)
    chart = reader.get_chart_data(
        "TEST",
        indicator_specs=[
            {"name": "stochastic", "params": {"period": 5, "k_smoothing": 3, "d_period": 3}},
        ],
        start=datetime(2024, 8, 1, tzinfo=timezone.utc),
        end=datetime(2024, 8, 20, tzinfo=timezone.utc),
    )
    names = {s.name for s in chart.series}
    assert names == {"stochastic_k", "stochastic_d"}


def test_get_chart_data_macd_decomposes_to_three_series() -> None:
    bars = _bars("TEST", [100.0 + i for i in range(30)])
    reader = _stub_reader(bars)
    chart = reader.get_chart_data(
        "TEST",
        indicator_specs=[
            {"name": "macd", "params": {"fast": 5, "slow": 10, "signal": 3}},
        ],
        start=datetime(2024, 8, 1, tzinfo=timezone.utc),
        end=datetime(2024, 8, 30, tzinfo=timezone.utc),
    )
    names = {s.name for s in chart.series}
    assert names == {"macd", "macd_signal", "macd_histogram"}


def test_get_chart_data_handles_unknown_indicator_with_error_series() -> None:
    """An unknown indicator surfaces as an empty series with an error label, not a crash."""
    bars = _bars("TEST", [10.0, 11.0, 12.0])
    reader = _stub_reader(bars)
    chart = reader.get_chart_data(
        "TEST",
        indicator_specs=[
            {"name": "sma", "params": {"period": 2}},
            {"name": "supertrend", "params": {}},  # unknown
        ],
        start=datetime(2024, 8, 1, tzinfo=timezone.utc),
        end=datetime(2024, 8, 4, tzinfo=timezone.utc),
    )
    # SMA should produce its series; supertrend produces an error stub.
    by_name = {s.name: s for s in chart.series}
    assert "sma" in by_name
    assert by_name["sma"].count == 3
    assert "supertrend" in by_name
    assert by_name["supertrend"].count == 0
    assert "error" in by_name["supertrend"].label


def test_get_chart_data_atr_uses_high_low() -> None:
    """ATR requires high/low — IndicatorReader must pass them through."""
    bars = []
    for i in range(20):
        bars.append(BronzeBar(
            symbol="TEST",
            timestamp=datetime(2024, 8, i + 1, tzinfo=timezone.utc),
            open=100.0, high=101.0, low=99.0, close=100.0,
            volume=1000.0, source="test",
        ))
    reader = _stub_reader(bars)
    chart = reader.get_chart_data(
        "TEST",
        indicator_specs=[{"name": "atr", "params": {"period": 5}}],
        start=datetime(2024, 8, 1, tzinfo=timezone.utc),
        end=datetime(2024, 8, 20, tzinfo=timezone.utc),
    )
    atr_series = chart.series[0]
    assert atr_series.name == "atr"
    # H-L=2 every bar, no gaps → ATR converges to 2.
    last_value = next(v for v in reversed(atr_series.values) if v.value is not None)
    assert last_value.value == pytest.approx(2.0, abs=1e-6)


# ─────────────────────────────────────────────────────────────────────
# HTTP routes
# ─────────────────────────────────────────────────────────────────────


def _make_app(stub_reader_func) -> FastAPI:
    app = FastAPI()
    app.include_router(indicators_router, prefix="/api", tags=["Indicators"])
    app.dependency_overrides[_route_reader_dep] = stub_reader_func
    return app


def test_route_get_series_basic() -> None:
    bars = _bars("TEST", [10.0, 12.0, 14.0, 16.0, 18.0])
    stub = _stub_reader(bars)
    app = _make_app(lambda: stub)
    with TestClient(app) as client:
        resp = client.get(
            "/api/indicators/series",
            params={
                "symbol": "TEST",
                "start": "2024-08-01T00:00:00Z",
                "end": "2024-08-06T00:00:00Z",
                "indicator": "sma",
                "interval": "1d",
                "params": json.dumps({"period": 3}),
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["name"] == "sma"
    assert body["count"] == 5


def test_route_get_series_400_on_unknown_indicator() -> None:
    bars = _bars("TEST", [10.0, 11.0])
    stub = _stub_reader(bars)
    app = _make_app(lambda: stub)
    with TestClient(app) as client:
        resp = client.get(
            "/api/indicators/series",
            params={
                "symbol": "TEST",
                "start": "2024-08-01T00:00:00Z",
                "end": "2024-08-03T00:00:00Z",
                "indicator": "supertrend",
            },
        )
    assert resp.status_code == 400
    assert "Unknown indicator" in resp.json()["detail"]


def test_route_get_series_400_on_bad_params_json() -> None:
    bars = _bars("TEST", [10.0, 11.0])
    stub = _stub_reader(bars)
    app = _make_app(lambda: stub)
    with TestClient(app) as client:
        resp = client.get(
            "/api/indicators/series",
            params={
                "symbol": "TEST",
                "start": "2024-08-01T00:00:00Z",
                "end": "2024-08-03T00:00:00Z",
                "indicator": "sma",
                "params": "{not-valid-json",
            },
        )
    assert resp.status_code == 400
    assert "valid JSON" in resp.json()["detail"]


def test_route_chart_data_returns_bars_and_multiple_series() -> None:
    bars = _bars("TEST", [float(c) for c in range(1, 25)])
    stub = _stub_reader(bars)
    app = _make_app(lambda: stub)
    with TestClient(app) as client:
        resp = client.post(
            "/api/indicators/chart-data",
            json={
                "symbol": "TEST",
                "start": "2024-08-01T00:00:00Z",
                "end": "2024-08-24T00:00:00Z",
                "interval": "1d",
                "indicators": [
                    {"name": "sma", "params": {"period": 5}, "label": "Fast"},
                    {"name": "bollinger", "params": {"period": 10}},
                ],
            },
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["symbol"] == "TEST"
    assert len(body["bars"]) == 24
    names = {s["name"] for s in body["series"]}
    # 1 SMA + 5 bollinger components.
    assert "sma" in names
    assert {"bollinger_upper", "bollinger_middle", "bollinger_lower"} <= names
    # Custom label honored on the SMA spec.
    sma_series = next(s for s in body["series"] if s["name"] == "sma")
    assert sma_series["label"] == "Fast"


# ─────────────────────────────────────────────────────────────────────
# MCP tools
# ─────────────────────────────────────────────────────────────────────


def test_mcp_tools_registered() -> None:
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert {"compute_indicator", "compute_indicators", "get_chart_data"} <= names


def test_mcp_compute_indicator(monkeypatch) -> None:
    bars = _bars("TEST", [10.0, 12.0, 14.0, 16.0, 18.0])
    stub = _stub_reader(bars)
    from app.mcp.tools import indicators as ind_tools
    ind_tools._reader.cache_clear()
    monkeypatch.setattr(ind_tools, "_reader", lambda: stub)

    body = _unwrap(asyncio.run(mcp.call_tool("compute_indicator", {
        "symbol": "TEST",
        "indicator": "sma",
        "start": "2024-08-01T00:00:00Z",
        "end": "2024-08-06T00:00:00Z",
        "interval": "1d",
        "params": {"period": 3},
    })))
    assert body["name"] == "sma"
    assert body["count"] == 5


def test_mcp_compute_indicators_decomposes_bollinger(monkeypatch) -> None:
    bars = _bars("TEST", [float(c) for c in range(1, 25)])
    stub = _stub_reader(bars)
    from app.mcp.tools import indicators as ind_tools
    ind_tools._reader.cache_clear()
    monkeypatch.setattr(ind_tools, "_reader", lambda: stub)

    body = _unwrap(asyncio.run(mcp.call_tool("compute_indicators", {
        "symbol": "TEST",
        "indicators": [
            {"name": "sma", "params": {"period": 5}},
            {"name": "bollinger", "params": {"period": 10}},
        ],
        "start": "2024-08-01T00:00:00Z",
        "end": "2024-08-24T00:00:00Z",
        "interval": "1d",
    })))
    names = {s["name"] for s in body["series"]}
    assert "sma" in names
    assert "bollinger_upper" in names
    assert "bollinger_middle" in names
    assert "bollinger_lower" in names
    assert len(body["bars"]) == 24


def test_mcp_get_chart_data_uses_lookback(monkeypatch) -> None:
    """get_chart_data resolves lookback_days into a window and calls compute_indicators."""
    bars = _bars("TEST", [float(c) for c in range(1, 25)])
    stub = _stub_reader(bars)
    from app.mcp.tools import indicators as ind_tools
    ind_tools._reader.cache_clear()
    monkeypatch.setattr(ind_tools, "_reader", lambda: stub)

    body = _unwrap(asyncio.run(mcp.call_tool("get_chart_data", {
        "symbol": "TEST",
        "interval": "1d",
        "lookback_days": 30,
        "indicators": [{"name": "sma", "params": {"period": 5}}],
    })))
    assert body["symbol"] == "TEST"
    names = {s["name"] for s in body["series"]}
    assert "sma" in names


# ─────────────────────────────────────────────────────────────────────
# Cross-consumer consistency gate
# ─────────────────────────────────────────────────────────────────────


def test_route_and_mcp_return_byte_identical_values(monkeypatch) -> None:
    """
    GATE: same indicator + same bars queried via HTTP route AND MCP
    tool produce IDENTICAL IndicatorSeries values. Locks in the
    "single source of truth" property — both surfaces hit the same
    IndicatorReader.
    """
    bars = _bars("TEST", [float(c) for c in range(100, 130)])

    # MCP path: patch the MCP tool's reader factory.
    stub_for_mcp = _stub_reader(bars)
    from app.mcp.tools import indicators as ind_tools
    ind_tools._reader.cache_clear()
    monkeypatch.setattr(ind_tools, "_reader", lambda: stub_for_mcp)

    mcp_body = _unwrap(asyncio.run(mcp.call_tool("compute_indicator", {
        "symbol": "TEST",
        "indicator": "sma",
        "start": "2024-08-01T00:00:00Z",
        "end": "2024-08-30T00:00:00Z",
        "interval": "1d",
        "params": {"period": 7},
    })))

    # HTTP path: TestClient with overridden dependency.
    stub_for_http = _stub_reader(bars)
    app = _make_app(lambda: stub_for_http)
    with TestClient(app) as client:
        resp = client.get(
            "/api/indicators/series",
            params={
                "symbol": "TEST",
                "start": "2024-08-01T00:00:00Z",
                "end": "2024-08-30T00:00:00Z",
                "indicator": "sma",
                "interval": "1d",
                "params": json.dumps({"period": 7}),
            },
        )
    http_body = resp.json()

    # Identical name + label + count.
    assert mcp_body["name"] == http_body["name"]
    assert mcp_body["label"] == http_body["label"]
    assert mcp_body["count"] == http_body["count"]
    # Every (timestamp, value) pair matches byte-for-byte.
    assert len(mcp_body["values"]) == len(http_body["values"])
    for m, h in zip(mcp_body["values"], http_body["values"]):
        assert m["timestamp"] == h["timestamp"]
        # Both should be None or both equal — no float drift.
        if m["value"] is None:
            assert h["value"] is None
        else:
            assert m["value"] == h["value"]
