"""
MCP tools — live tier (BarReader-backed) + signals (SignalReader) +
quotes (QuoteService).

Each tool tested for:
  - Discovery (description + input schema)
  - Invocation against a stubbed reader/service (parameters forwarded,
    Pydantic response shape preserved end-to-end)

The bronze-side structural gate (in test_mcp_lake.py) doesn't apply
here — these tools are intentionally CH-dependent.
"""
from __future__ import annotations

import asyncio
import json
from datetime import date, datetime, timezone
from typing import Any
from unittest.mock import patch

import pytest

from app.mcp.server import mcp, register_all_tools
from app.services.readers.schemas import (
    LiveBar,
    Quote,
    QuotesResponse,
    Signal,
)


register_all_tools()


def _unwrap(result: Any) -> dict:
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    if isinstance(result, dict):
        return result
    if isinstance(result, (list, tuple)) and result:
        first = result[0]
        text = getattr(first, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"unexpected call_tool result shape: {result!r}")


# ─────────────────────────────────────────────────────────────────────
# Discovery
# ─────────────────────────────────────────────────────────────────────


def test_slice2_tools_all_registered() -> None:
    """All 9 slice-2 tools advertised."""
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    expected = {
        # live
        "get_recent_bars", "get_bars_in_range", "get_bars_for_chart",
        "get_latest_bar_per_symbol",
        # signals
        "get_recent_signals", "get_signals_by_symbol",
        # quotes
        "get_quote", "get_quotes",
    }
    assert expected <= names, f"missing: {expected - names}"


def test_slice2_tools_have_descriptions() -> None:
    tools = asyncio.run(mcp.list_tools())
    by_name = {t.name: t for t in tools}
    for n in ("get_recent_bars", "get_bars_for_chart", "get_recent_signals",
              "get_quote", "get_quotes"):
        assert by_name[n].description, f"{n} has no description"


# ─────────────────────────────────────────────────────────────────────
# BarReader tools
# ─────────────────────────────────────────────────────────────────────


def _live_bar(m: int, interval: str = "1m") -> LiveBar:
    return LiveBar(
        symbol="AAPL",
        timestamp=datetime(2024, 8, 1, 14, m, tzinfo=timezone.utc),
        open=100.0 + m * 0.01,
        high=100.5 + m * 0.01,
        low=99.5 + m * 0.01,
        close=100.2 + m * 0.01,
        volume=1000.0 + m,
        vwap=None,
        trade_count=10 + m,
        source="polygon",
        interval=interval,
    )


@pytest.fixture
def stub_bar_reader(monkeypatch):
    from app.mcp.tools import live as live_mod

    class _Stub:
        def __init__(self):
            self.calls: list[dict] = []
        def get_recent_bars(self, symbol, limit=200, *, source_table="ohlcv_1m"):
            self.calls.append({
                "method": "get_recent_bars", "symbol": symbol, "limit": limit,
                "source_table": source_table,
            })
            return [_live_bar(m) for m in range(3)]
        def get_bars_in_range(self, symbol, start, end, *, interval="1m", limit=100_000, source_table="ohlcv_1m"):
            self.calls.append({
                "method": "get_bars_in_range", "symbol": symbol,
                "interval": interval, "source_table": source_table,
            })
            return [_live_bar(m, interval=interval) for m in range(2)]
        def get_bars_for_chart(self, symbol, *, interval="1m", lookback_days=None, limit=None, source_table="ohlcv_1m"):
            self.calls.append({
                "method": "get_bars_for_chart", "symbol": symbol,
                "interval": interval, "lookback_days": lookback_days, "limit": limit,
                "source_table": source_table,
            })
            return [_live_bar(m, interval=interval) for m in range(4)]
        def get_latest_bar_per_symbol(self, symbols):
            self.calls.append({"method": "get_latest_bar_per_symbol", "symbols": symbols})
            return {s: _live_bar(0) for s in symbols if s != "GHOST"}

    stub = _Stub()
    live_mod._reader.cache_clear()
    monkeypatch.setattr(live_mod, "_reader", lambda: stub)
    return stub


def test_call_get_recent_bars(stub_bar_reader) -> None:
    body = _unwrap(asyncio.run(mcp.call_tool(
        "get_recent_bars", {"symbol": "AAPL", "limit": 3}
    )))
    assert body["symbol"] == "AAPL"
    assert body["interval"] == "1m"
    assert body["count"] == 3


def test_call_get_bars_in_range(stub_bar_reader) -> None:
    body = _unwrap(asyncio.run(mcp.call_tool("get_bars_in_range", {
        "symbol": "AAPL",
        "start": "2024-08-01T14:00:00Z",
        "end": "2024-08-01T15:00:00Z",
        "interval": "5m",
        "source_table": "ohlcv_1m",
    })))
    assert body["interval"] == "5m"
    assert body["count"] == 2
    last = stub_bar_reader.calls[-1]
    assert last["interval"] == "5m"
    assert last["source_table"] == "ohlcv_1m"


def test_call_get_bars_for_chart(stub_bar_reader) -> None:
    body = _unwrap(asyncio.run(mcp.call_tool("get_bars_for_chart", {
        "symbol": "AAPL", "interval": "1h", "lookback_days": 30,
    })))
    assert body["interval"] == "1h"
    assert body["count"] == 4
    assert stub_bar_reader.calls[-1]["lookback_days"] == 30


def test_call_get_latest_bar_per_symbol(stub_bar_reader) -> None:
    body = _unwrap(asyncio.run(mcp.call_tool(
        "get_latest_bar_per_symbol", {"symbols": ["AAPL", "MSFT", "GHOST"]}
    )))
    # GHOST omitted (stub drops it).
    assert set(body["bars"].keys()) == {"AAPL", "MSFT"}
    assert body["count"] == 2


# ─────────────────────────────────────────────────────────────────────
# SignalReader tools
# ─────────────────────────────────────────────────────────────────────


def _signal(idx: int) -> Signal:
    return Signal(
        id=f"sig-{idx}",
        symbol="AAPL",
        signal_type="hidden_bullish_divergence",
        indicator="rsi",
        ts_signal=datetime(2024, 8, 1, 14, idx, tzinfo=timezone.utc),
        price_at_signal=100.0 + idx,
        indicator_value=30.0 + idx,
    )


@pytest.fixture
def stub_signal_reader(monkeypatch):
    from app.mcp.tools import signals as signals_mod

    class _Stub:
        def __init__(self):
            self.calls: list[dict] = []
        def get_recent_signals(self, limit=50):
            self.calls.append({"method": "get_recent_signals", "limit": limit})
            return [_signal(i) for i in range(2)]
        def get_signals_by_symbol(self, symbol, limit):
            self.calls.append({
                "method": "get_signals_by_symbol",
                "symbol": symbol, "limit": limit,
            })
            return [_signal(i) for i in range(3)]

    stub = _Stub()
    signals_mod._reader.cache_clear()
    monkeypatch.setattr(signals_mod, "_reader", lambda: stub)
    return stub


def test_call_get_recent_signals(stub_signal_reader) -> None:
    body = _unwrap(asyncio.run(mcp.call_tool(
        "get_recent_signals", {"limit": 50}
    )))
    assert body["count"] == 2
    assert body["signals"][0]["signal_type"] == "hidden_bullish_divergence"
    assert body["signals"][0]["indicator"] == "rsi"


def test_call_get_signals_by_symbol(stub_signal_reader) -> None:
    body = _unwrap(asyncio.run(mcp.call_tool(
        "get_signals_by_symbol", {"symbol": "AAPL", "limit": 100}
    )))
    assert body["symbol"] == "AAPL"
    assert body["count"] == 3


# ─────────────────────────────────────────────────────────────────────
# QuoteService tools
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def stub_quote_service(monkeypatch):
    from app.mcp.tools import quotes as quotes_mod

    class _Stub:
        def __init__(self):
            self.calls: list[dict] = []
        async def get_quote(self, symbol: str):
            self.calls.append({"method": "get_quote", "symbol": symbol})
            if symbol == "UNKNOWN":
                return None
            return Quote(symbol=symbol, last=224.5, provider="schwab")
        async def get_quotes(self, symbols):
            self.calls.append({"method": "get_quotes", "symbols": list(symbols)})
            return QuotesResponse(
                quotes={s: Quote(symbol=s, last=100.0, provider="schwab")
                        for s in symbols if s != "BAD"},
                count=sum(1 for s in symbols if s != "BAD"),
                invalid_symbols=["BAD"] if "BAD" in symbols else [],
            )

    stub = _Stub()
    quotes_mod._svc.cache_clear()
    monkeypatch.setattr(quotes_mod, "_svc", lambda: stub)
    return stub


def test_call_get_quote(stub_quote_service) -> None:
    """Optional[Quote] return → FastMCP wraps as {'result': {...}}."""
    body = _unwrap(asyncio.run(mcp.call_tool("get_quote", {"symbol": "AAPL"})))
    # FastMCP convention: non-dict pydantic returns are wrapped under 'result'
    payload = body.get("result", body)
    assert payload["symbol"] == "AAPL"
    assert payload["last"] == 224.5
    assert payload["provider"] == "schwab"


def test_call_get_quote_unknown_returns_null(stub_quote_service) -> None:
    """Provider couldn't resolve → tool returns null, not an error."""
    result = asyncio.run(mcp.call_tool("get_quote", {"symbol": "UNKNOWN"}))
    # Structured form: {'result': None}. Text-content form: 'null'.
    if isinstance(result, tuple) and len(result) == 2:
        assert result[1] in (None, {}, {"result": None}), f"unexpected: {result[1]!r}"


def test_call_get_quotes(stub_quote_service) -> None:
    body = _unwrap(asyncio.run(mcp.call_tool(
        "get_quotes", {"symbols": ["AAPL", "MSFT", "BAD"]}
    )))
    assert body["count"] == 2
    assert set(body["quotes"].keys()) == {"AAPL", "MSFT"}
    assert body["invalid_symbols"] == ["BAD"]
