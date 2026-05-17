"""
MCP server unit tests — `app/mcp/tools/lake.py` slice.

Three classes of test:

  1. **Discovery.** `list_tools()` returns the 3 lake tools with
     descriptions an agent can read. Smoke-checks that the FastMCP
     registration ran and that the docstrings make it through.

  2. **Invocation.** `call_tool(name, args)` with a stubbed
     BronzeReader returns the expected Pydantic shape end-to-end.
     Exercises the full FastMCP request/response path.

  3. **Structural gate.** Walks every module reachable from
     `app/mcp/tools/lake.py` and asserts none sit under `app.db.*`.
     Parallels the gate test on `routes_lake` — locks in CH-independence
     of the bronze tool path at the code-structure level.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import json
from datetime import datetime, timezone
from typing import Any

import pytest

from app.mcp import tools as _tools_pkg  # noqa: F401 — ensure tools/__init__ resolves
from app.mcp.server import mcp, register_all_tools
from app.services.readers.schemas import BronzeBar


# Ensure tool registration has happened for the test session.
register_all_tools()


# ─────────────────────────────────────────────────────────────────────
# Discovery
# ─────────────────────────────────────────────────────────────────────


def test_list_tools_returns_three_lake_tools() -> None:
    """`list_tools` advertises the lake slice."""
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert {"get_bronze_bars", "list_bronze_symbols", "get_latest_trading_day"} <= names


def test_each_lake_tool_has_descriptive_docstring() -> None:
    """Tool descriptions are agent UI — first sentence visible in list_tools."""
    tools = asyncio.run(mcp.list_tools())
    by_name = {t.name: t for t in tools}
    for name in ("get_bronze_bars", "list_bronze_symbols", "get_latest_trading_day"):
        tool = by_name[name]
        assert tool.description, f"{name} has no description"
        # First line should be short + descriptive
        first_line = tool.description.split("\n", 1)[0]
        assert 10 < len(first_line) < 120, (
            f"{name}: first line of description should be a one-sentence summary"
        )


def test_each_lake_tool_has_input_schema() -> None:
    """The JSON Schema the LLM sees should cover required + optional args."""
    tools = asyncio.run(mcp.list_tools())
    by_name = {t.name: t for t in tools}

    bars_schema = by_name["get_bronze_bars"].inputSchema
    assert "symbol" in bars_schema["properties"]
    assert "start" in bars_schema["properties"]
    assert "end" in bars_schema["properties"]
    assert set(bars_schema.get("required", [])) >= {"symbol", "start", "end"}

    syms_schema = by_name["list_bronze_symbols"].inputSchema
    assert "provider" in syms_schema["properties"]
    assert "since" in syms_schema["properties"]

    last_schema = by_name["get_latest_trading_day"].inputSchema
    assert "provider" in last_schema["properties"]
    assert "lookback_days" in last_schema["properties"]


# ─────────────────────────────────────────────────────────────────────
# Invocation (with stubbed reader)
# ─────────────────────────────────────────────────────────────────────


class _StubReader:
    """Mirrors the slice of BronzeReader the lake tools use."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def get_bars(self, symbol, start, end, *, provider="polygon", limit=None):
        self.calls.append({
            "method": "get_bars", "symbol": symbol, "provider": provider,
        })
        return [
            BronzeBar(
                symbol=symbol,
                timestamp=datetime(2024, 8, 1, 14, m, tzinfo=timezone.utc),
                open=100.0 + m * 0.01,
                high=100.5 + m * 0.01,
                low=99.5 + m * 0.01,
                close=100.2 + m * 0.01,
                volume=1000.0 + m,
                vwap=None,
                trade_count=10 + m,
                source="polygon-flatfiles",
            )
            for m in range(2)
        ]

    def list_symbols(self, *, provider="polygon", since=None, limit=None):
        self.calls.append({
            "method": "list_symbols", "provider": provider,
            "since": since, "limit": limit,
        })
        return ["AAPL", "MSFT", "NVDA"]

    def latest_trading_day(self, *, provider="polygon", lookback_days=14):
        from datetime import date as _date
        self.calls.append({"method": "latest_trading_day", "provider": provider})
        return _date(2024, 8, 14)


@pytest.fixture
def stub_reader(monkeypatch):
    """Patch the lake module's `_reader()` to return a stub."""
    from app.mcp.tools import lake as lake_mod

    stub = _StubReader()
    lake_mod._reader.cache_clear()
    monkeypatch.setattr(lake_mod, "_reader", lambda: stub)
    return stub


def _unwrap(result: Any) -> dict:
    """FastMCP returns a tuple-ish thing; extract the structured dict."""
    # call_tool returns a tuple of (content_blocks, structured_dict) in
    # the current FastMCP version, or just structured_dict in older ones.
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    if isinstance(result, dict):
        return result
    # Fallback: parse from the first TextContent block.
    if isinstance(result, (list, tuple)) and result:
        first = result[0]
        text = getattr(first, "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"unexpected call_tool result shape: {type(result)} {result!r}")


def test_call_get_bronze_bars(stub_reader) -> None:
    result = asyncio.run(mcp.call_tool(
        "get_bronze_bars",
        {
            "symbol": "AAPL",
            "start": "2024-08-01T14:00:00Z",
            "end": "2024-08-01T15:00:00Z",
            "provider": "polygon",
            "limit": 10,
        },
    ))
    body = _unwrap(result)
    assert body["symbol"] == "AAPL"
    assert body["provider"] == "polygon"
    assert body["count"] == 2
    assert len(body["bars"]) == 2
    assert body["bars"][0]["symbol"] == "AAPL"
    assert body["bars"][0]["source"] == "polygon-flatfiles"

    # Stub got the right call.
    assert stub_reader.calls[0]["method"] == "get_bars"


def test_call_list_bronze_symbols(stub_reader) -> None:
    result = asyncio.run(mcp.call_tool(
        "list_bronze_symbols",
        {"provider": "polygon", "limit": 100},
    ))
    body = _unwrap(result)
    assert body["provider"] == "polygon"
    assert body["count"] == 3
    assert body["symbols"] == ["AAPL", "MSFT", "NVDA"]
    # Echoed `since` resolves to a default (30 days back) when omitted.
    assert body["since"] is not None


def test_call_get_latest_trading_day(stub_reader) -> None:
    result = asyncio.run(mcp.call_tool(
        "get_latest_trading_day",
        {"provider": "polygon", "lookback_days": 14},
    ))
    body = _unwrap(result)
    assert body["provider"] == "polygon"
    assert body["latest_trading_day"] == "2024-08-14"


def test_unknown_tool_raises() -> None:
    """Calling a tool that isn't registered fails loudly."""
    with pytest.raises(Exception):  # FastMCP raises ToolError
        asyncio.run(mcp.call_tool("nonexistent_tool", {}))


# ─────────────────────────────────────────────────────────────────────
# Structural gate — same pattern as test_routes_lake.py
# ─────────────────────────────────────────────────────────────────────


def test_lake_tools_module_does_not_import_clickhouse() -> None:
    """
    GATE: every `app.*` module transitively reachable from
    `app/mcp/tools/lake.py` must NOT sit under `app.db.*`. Bronze tools
    are the load-bearing "agent can read history when CH is down" path
    — a regression in the wrong direction breaks ML training.

    Same AST-walk pattern as `test_lake_route_does_not_import_clickhouse`
    in `tests/test_routes_lake.py`. Conservative: if a tool author
    accidentally adds a CH import to the bronze tool path, this fails
    before any production code runs.
    """
    visited: set[str] = set()
    forbidden_prefixes = ("app.db",)

    def _module_path(mod_name: str) -> str | None:
        try:
            spec = importlib.util.find_spec(mod_name)
        except (ImportError, ValueError):
            return None
        if spec is None or spec.origin in (None, "built-in"):
            return None
        return spec.origin

    def _walk(mod_name: str) -> None:
        if mod_name in visited:
            return
        visited.add(mod_name)
        path = _module_path(mod_name)
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read(), filename=path)
        except (OSError, SyntaxError):
            return
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("app."):
                        _walk(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module and node.module.startswith("app."):
                    _walk(node.module)

    _walk("app.mcp.tools.lake")

    leaked = sorted(m for m in visited if any(m.startswith(p) for p in forbidden_prefixes))
    assert not leaked, (
        f"CH-independence regression: {len(leaked)} module(s) under "
        f"{forbidden_prefixes} are reachable from app.mcp.tools.lake.\n"
        "The bronze MCP tools MUST NOT depend on ClickHouse. Move any "
        "CH-bound logic to a CH-backed reader + a separate tool file "
        "(`tools/live.py`, etc.). Leaked: " + str(leaked)
    )
