"""
MCP tools — discovery + observability slice (slice 3).

Covers the 12 tools across watchlist / movers / instruments /
market / coverage / system. Same stub-and-call pattern as
test_mcp_lake.py + test_mcp_live.py.

Most provider-backed tools (movers, instruments, market_hours) have
a degraded-mode contract: returns `{}` on any provider error rather
than raising. Tests cover both the success path (stubbed provider
returns shape) and the degraded path (provider missing the method).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone, date
from typing import Any
from unittest.mock import patch

import pytest

from app.mcp.server import mcp, register_all_tools


register_all_tools()


def _unwrap(result: Any) -> Any:
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    if isinstance(result, dict):
        return result
    if isinstance(result, (list, tuple)) and result:
        first = result[0]
        text = getattr(first, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"unexpected: {result!r}")


# ─────────────────────────────────────────────────────────────────────
# Discovery — slice 3 adds 12 tools
# ─────────────────────────────────────────────────────────────────────


def test_slice3_tools_registered() -> None:
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    expected = {
        # watchlist
        "list_watchlists", "get_watchlist", "get_watchlist_members",
        # movers
        "get_movers",
        # instruments
        "search_instrument", "get_instruments",
        # market
        "get_market_hours",
        # coverage
        "get_coverage", "find_intraday_gaps", "get_bronze_table_stats",
        # system
        "get_health", "get_lake_freshness",
    }
    assert expected <= names, f"missing: {expected - names}"


def test_total_tool_count() -> None:
    """Lock in the canonical surface — 23 tools across all 3 slices so far."""
    tools = asyncio.run(mcp.list_tools())
    # Allow growth, but flag drops (which usually means a registration regression).
    assert len(tools) >= 23, f"expected at least 23 tools, got {len(tools)}"


# ─────────────────────────────────────────────────────────────────────
# Watchlist
# ─────────────────────────────────────────────────────────────────────


def test_call_list_watchlists() -> None:
    fake_rows = [
        {"name": "default", "kind": "default", "is_active": True,
         "member_count": 5, "updated_at": datetime(2024, 8, 1, tzinfo=timezone.utc)},
        {"name": "swing", "kind": "user", "is_active": True,
         "member_count": 12, "updated_at": datetime(2024, 8, 5, tzinfo=timezone.utc)},
    ]
    with patch("app.services.live.watchlist_service.watchlist_service.list_watchlists",
               return_value=fake_rows):
        body = _unwrap(asyncio.run(mcp.call_tool("list_watchlists", {})))
    assert body["count"] == 2
    names = {w["name"] for w in body["watchlists"]}
    assert names == {"default", "swing"}


def test_call_get_watchlist() -> None:
    fake_wl = {"name": "swing", "kind": "user", "is_active": True,
               "updated_at": datetime(2024, 8, 5, tzinfo=timezone.utc)}
    fake_members = ["AAPL", "MSFT", "NVDA"]
    with patch("app.services.live.watchlist_service.watchlist_service.get_watchlist",
               return_value=fake_wl), \
         patch("app.services.live.watchlist_service.watchlist_service.list_members",
               return_value=fake_members):
        body = _unwrap(asyncio.run(mcp.call_tool("get_watchlist", {"name": "swing"})))
    payload = body.get("result", body)
    assert payload["name"] == "swing"
    assert payload["members"] == ["AAPL", "MSFT", "NVDA"]
    assert payload["member_count"] == 3


def test_call_get_watchlist_missing_returns_null() -> None:
    """get_watchlist returns null when the name doesn't exist."""
    with patch("app.services.live.watchlist_service.watchlist_service.get_watchlist",
               return_value=None):
        result = asyncio.run(mcp.call_tool("get_watchlist", {"name": "ghost"}))
    if isinstance(result, tuple) and len(result) == 2:
        assert result[1] in (None, {}, {"result": None})


def test_call_get_watchlist_members() -> None:
    with patch("app.services.live.watchlist_service.watchlist_service.list_members",
               return_value=["SPY", "QQQ"]):
        body = _unwrap(asyncio.run(mcp.call_tool(
            "get_watchlist_members", {"name": "default"}
        )))
    # list[str] returns wrapped in {'result': [...]}
    payload = body.get("result", body)
    assert payload == ["SPY", "QQQ"]


# ─────────────────────────────────────────────────────────────────────
# Movers / instruments / market hours — provider-backed
# ─────────────────────────────────────────────────────────────────────


class _FakeProvider:
    async def get_movers(self, symbol_id, **kwargs):
        return {"screeners": [{"symbol": "NVDA", "lastPrice": 100.0}]}
    async def search_instruments(self, query, *, limit=10):
        return [{"symbol": "AAPL", "description": "Apple Inc."}]
    async def get_instruments(self, symbols, projection, **kwargs):
        return {"instruments": [{"symbol": s, "description": f"Stub {s}"} for s in symbols]}
    async def get_market_hours(self, market_id=None):
        return {"equity": {"EQ": {"date": "2024-08-01", "isOpen": True}}}


def test_call_get_movers() -> None:
    with patch("app.mcp.tools.movers.get_provider", return_value=_FakeProvider()):
        body = _unwrap(asyncio.run(mcp.call_tool("get_movers", {"symbol_id": "$SPX"})))
    payload = body.get("result", body)
    assert "screeners" in payload
    assert payload["screeners"][0]["symbol"] == "NVDA"


def test_call_get_movers_provider_missing_returns_empty() -> None:
    class _Bare: pass
    with patch("app.mcp.tools.movers.get_provider", return_value=_Bare()):
        body = _unwrap(asyncio.run(mcp.call_tool("get_movers", {"symbol_id": "$SPX"})))
    payload = body.get("result", body)
    assert payload == {}


def test_call_search_instrument() -> None:
    with patch("app.mcp.tools.instruments.get_provider", return_value=_FakeProvider()):
        body = _unwrap(asyncio.run(mcp.call_tool(
            "search_instrument", {"query": "apple", "limit": 5}
        )))
    payload = body.get("result", body)
    assert payload[0]["symbol"] == "AAPL"


def test_call_get_instruments() -> None:
    with patch("app.mcp.tools.instruments.get_provider", return_value=_FakeProvider()):
        body = _unwrap(asyncio.run(mcp.call_tool(
            "get_instruments", {"symbols": ["AAPL", "MSFT"]}
        )))
    payload = body.get("result", body)
    syms = {i["symbol"] for i in payload["instruments"]}
    assert syms == {"AAPL", "MSFT"}


def test_call_get_market_hours() -> None:
    with patch("app.mcp.tools.market.get_provider", return_value=_FakeProvider()):
        body = _unwrap(asyncio.run(mcp.call_tool("get_market_hours", {})))
    payload = body.get("result", body)
    assert "equity" in payload


# ─────────────────────────────────────────────────────────────────────
# Coverage tools
# ─────────────────────────────────────────────────────────────────────


def test_call_get_coverage_1m() -> None:
    async def _fake_cov(symbol, start, end):
        return {
            "symbol": symbol, "start": start, "end": end,
            "earliest": datetime(2024, 8, 1, 13, 30, tzinfo=timezone.utc),
            "latest": datetime(2024, 8, 1, 20, 0, tzinfo=timezone.utc),
            "bar_count": 350,
        }
    with patch("app.db.queries.coverage_async", side_effect=_fake_cov):
        body = _unwrap(asyncio.run(mcp.call_tool("get_coverage", {
            "symbol": "AAPL",
            "start": "2024-08-01T13:30:00Z",
            "end": "2024-08-01T20:00:00Z",
            "interval": "1m",
        })))
    assert body["actual_bars"] == 350
    # 1 weekday * 390 expected
    assert body["expected_bars"] == 390
    assert body["coverage_pct"] is not None and 0 < body["coverage_pct"] <= 1
    assert body["first_bar"] is not None


def test_call_find_intraday_gaps() -> None:
    async def _fake_gaps(symbol, start, end, *, min_gap_minutes=5):
        return [
            {"start": datetime(2024, 8, 1, 14, 30, tzinfo=timezone.utc),
             "end":   datetime(2024, 8, 1, 14, 38, tzinfo=timezone.utc),
             "minutes": 8},
            {"start": datetime(2024, 8, 1, 16, 0, tzinfo=timezone.utc),
             "end":   datetime(2024, 8, 1, 16, 5, tzinfo=timezone.utc),
             "minutes": 5},
        ]
    with patch("app.db.queries.find_intraday_gaps_async", side_effect=_fake_gaps):
        body = _unwrap(asyncio.run(mcp.call_tool("find_intraday_gaps", {
            "symbol": "AAPL",
            "start": "2024-08-01T13:30:00Z",
            "end": "2024-08-01T20:00:00Z",
        })))
    assert len(body["gaps"]) == 2
    assert body["total_missing_minutes"] == 13


def test_call_get_bronze_table_stats_handles_error() -> None:
    """Unreachable table → BronzeTableStats with `error` field populated."""
    with patch("app.services.iceberg_catalog.get_catalog",
               side_effect=RuntimeError("no AWS creds")):
        body = _unwrap(asyncio.run(mcp.call_tool(
            "get_bronze_table_stats", {"table": "polygon_minute"}
        )))
    assert body["table_name"] == "polygon_minute"
    assert body["error"] is not None
    assert "RuntimeError" in body["error"]
    assert body["total_records"] is None


# ─────────────────────────────────────────────────────────────────────
# System
# ─────────────────────────────────────────────────────────────────────


def test_call_get_health_degraded_when_iceberg_down() -> None:
    """One side up + one down -> status='degraded'."""
    with patch("app.mcp.tools.system._ping_clickhouse",
               return_value=(True, None)), \
         patch("app.mcp.tools.system._ping_iceberg_catalog",
               return_value=(False, "RuntimeError: no AWS creds")):
        body = _unwrap(asyncio.run(mcp.call_tool("get_health", {})))
    assert body["clickhouse"] is True
    assert body["iceberg_catalog"] is False
    assert body["status"] == "degraded"
    by_name = {s["name"]: s for s in body["services"]}
    assert by_name["iceberg_catalog"]["detail"]


def test_call_get_health_all_down() -> None:
    """Both pings failing -> status='down'."""
    with patch("app.mcp.tools.system._ping_clickhouse",
               return_value=(False, "no ch")), \
         patch("app.mcp.tools.system._ping_iceberg_catalog",
               return_value=(False, "no aws")):
        body = _unwrap(asyncio.run(mcp.call_tool("get_health", {})))
    assert body["status"] == "down"


def test_call_get_health_all_up() -> None:
    with patch("app.mcp.tools.system._ping_clickhouse",
               return_value=(True, None)), \
         patch("app.mcp.tools.system._ping_iceberg_catalog",
               return_value=(True, None)):
        body = _unwrap(asyncio.run(mcp.call_tool("get_health", {})))
    assert body["status"] == "ok"


def test_call_get_lake_freshness() -> None:
    """Per-table date lookup; isolation on per-table errors."""
    class _StubReader:
        def latest_trading_day(self, *, provider="polygon", lookback_days=14):
            if provider == "polygon":
                return date(2026, 5, 15)
            raise RuntimeError("schwab table missing")

    with patch("app.services.readers.bronze_reader.BronzeReader.from_settings",
               return_value=_StubReader()):
        body = _unwrap(asyncio.run(mcp.call_tool("get_lake_freshness", {})))
    assert body["tables"]["polygon_minute"] == "2026-05-15"
    # Schwab failed -> null, not exception
    assert body["tables"]["schwab_minute"] is None
