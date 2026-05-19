"""Tests for `MCPToolRunner` and `_truncate`.

Acceptance criteria (slice 3):
  - Dispatch happy path — tool runs, result is JSON-serialised content.
  - Tool error → ToolResult.error populated, content is `{}`, no raise.
  - §8.4 truncation: list > 50 items is clipped; string > 5 000 chars is clipped.
  - The standard `mcp.tool: <name> ok in X.YYYs` log line appears (middleware reuse).
  - `get_tool_defs` returns Anthropic-format dicts for only the allowed names.
  - `ToolRunner` Protocol is satisfied by `MCPToolRunner` structurally.
"""
from __future__ import annotations

import json
import logging

import pytest
from mcp.server.fastmcp import FastMCP

from pydantic import BaseModel

from app.mcp.middleware import tool_call
from app.services.assistant.runner import (
    MCPToolRunner,
    ToolResult,
    ToolRunner,
    _truncate,
)


# ─────────────────────────────────────────────────────────────────────
# Fixtures — local FastMCP with controlled test tools
# ─────────────────────────────────────────────────────────────────────


class _ItemsResponse(BaseModel):
    """Pydantic response used by test tools — same pattern as real MCP tools."""
    items: list[str]
    count: int


@pytest.fixture()
def test_mcp() -> FastMCP:
    """Fresh FastMCP instance with only test tools — no real readers."""
    server = FastMCP("test-runner")

    @server.tool()
    def echo(msg: str) -> str:
        """Echo the input."""
        with tool_call("echo", msg=msg):
            return msg

    @server.tool()
    def always_fails(reason: str) -> str:
        """Always raises a ValueError."""
        with tool_call("always_fails"):
            raise ValueError(reason)

    @server.tool()
    def pydantic_with_long_list(n: int) -> _ItemsResponse:
        """Return a Pydantic model whose 'items' key holds n strings."""
        with tool_call("pydantic_with_long_list", n=n):
            return _ItemsResponse(items=[f"x_{i}" for i in range(n)], count=n)

    return server


@pytest.fixture()
def runner(test_mcp: FastMCP) -> MCPToolRunner:
    return MCPToolRunner(test_mcp)


# ─────────────────────────────────────────────────────────────────────
# Protocol conformance
# ─────────────────────────────────────────────────────────────────────


def test_mcp_tool_runner_satisfies_tool_runner_protocol(runner: MCPToolRunner) -> None:
    assert isinstance(runner, ToolRunner)


# ─────────────────────────────────────────────────────────────────────
# get_tool_defs
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_tool_defs_returns_only_allowed(runner: MCPToolRunner) -> None:
    defs = await runner.get_tool_defs(["echo", "pydantic_with_long_list"])
    names = [d["name"] for d in defs]
    assert set(names) == {"echo", "pydantic_with_long_list"}
    assert "always_fails" not in names


@pytest.mark.asyncio
async def test_get_tool_defs_anthropic_schema_shape(runner: MCPToolRunner) -> None:
    defs = await runner.get_tool_defs(["echo"])
    assert len(defs) == 1
    d = defs[0]
    assert d["name"] == "echo"
    assert isinstance(d["description"], str)
    assert "input_schema" in d
    assert d["input_schema"]["type"] == "object"


@pytest.mark.asyncio
async def test_get_tool_defs_empty_when_no_names_allowed(runner: MCPToolRunner) -> None:
    defs = await runner.get_tool_defs([])
    assert defs == []


@pytest.mark.asyncio
async def test_get_tool_defs_unknown_name_excluded(runner: MCPToolRunner) -> None:
    defs = await runner.get_tool_defs(["nonexistent_tool"])
    assert defs == []


# ─────────────────────────────────────────────────────────────────────
# run — happy path
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_happy_path_returns_tool_result(runner: MCPToolRunner) -> None:
    tr = await runner.run("tc_1", "echo", {"msg": "hello"})
    assert isinstance(tr, ToolResult)
    assert tr.tool_call_id == "tc_1"
    assert tr.name == "echo"
    assert tr.error is None
    assert tr.truncated is False
    assert tr.elapsed_s >= 0.0


@pytest.mark.asyncio
async def test_run_content_is_json_string(runner: MCPToolRunner) -> None:
    tr = await runner.run("tc_1", "echo", {"msg": "world"})
    parsed = json.loads(tr.content)
    assert isinstance(parsed, dict)


# ─────────────────────────────────────────────────────────────────────
# run — error path
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_tool_error_sets_error_field(runner: MCPToolRunner) -> None:
    tr = await runner.run("tc_err", "always_fails", {"reason": "kaboom"})
    assert tr.error is not None
    assert "kaboom" in tr.error
    assert tr.content == "{}"


@pytest.mark.asyncio
async def test_run_tool_error_does_not_raise(runner: MCPToolRunner) -> None:
    """Errors must come back as ToolResult, never propagate to the caller."""
    tr = await runner.run("tc_e", "always_fails", {"reason": "boom"})
    assert isinstance(tr, ToolResult)


# ─────────────────────────────────────────────────────────────────────
# run — middleware log line
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_emits_middleware_log_line(
    runner: MCPToolRunner, caplog: pytest.LogCaptureFixture
) -> None:
    """The `mcp.tool: <name> ok in X.YYYs` line proves middleware reuse."""
    with caplog.at_level(logging.INFO, logger="mcp.tools"):
        await runner.run("tc_log", "echo", {"msg": "test"})

    assert any(
        "mcp.tool: echo ok" in record.message for record in caplog.records
    ), "Expected 'mcp.tool: echo ok ...' in mcp.tools log output"


# ─────────────────────────────────────────────────────────────────────
# §8.4 truncation — _truncate unit tests
# ─────────────────────────────────────────────────────────────────────


def test_truncate_short_list_unchanged() -> None:
    d = {"items": list(range(10))}
    text, truncated = _truncate(d)
    assert truncated is False
    parsed = json.loads(text)
    assert len(parsed["items"]) == 10


def test_truncate_long_list_clips_to_50() -> None:
    d = {"items": list(range(100))}
    text, truncated = _truncate(d)
    assert truncated is True
    parsed = json.loads(text)
    assert len(parsed["items"]) == 50
    assert parsed.get("_truncated") is True


def test_truncate_exactly_50_items_not_truncated() -> None:
    d = {"items": list(range(50))}
    text, truncated = _truncate(d)
    assert truncated is False


def test_truncate_51_items_is_truncated() -> None:
    d = {"items": list(range(51))}
    _, truncated = _truncate(d)
    assert truncated is True


def test_truncate_long_string_clips_at_5000() -> None:
    d = {"text": "x" * 6000}
    text, truncated = _truncate(d)
    assert truncated is True
    assert len(text) <= 5000


def test_truncate_short_string_unchanged() -> None:
    d = {"text": "short"}
    text, truncated = _truncate(d)
    assert truncated is False
    assert json.loads(text)["text"] == "short"


def test_truncate_multiple_list_fields() -> None:
    d = {"a": list(range(60)), "b": list(range(60))}
    text, truncated = _truncate(d)
    assert truncated is True
    parsed = json.loads(text)
    assert len(parsed["a"]) == 50
    assert len(parsed["b"]) == 50


# ─────────────────────────────────────────────────────────────────────
# §8.4 truncation — via runner.run
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_truncates_long_list_result(runner: MCPToolRunner) -> None:
    tr = await runner.run("tc_t", "pydantic_with_long_list", {"n": 100})
    assert tr.truncated is True
    parsed = json.loads(tr.content)
    assert len(parsed["items"]) == 50
    assert parsed.get("_truncated") is True


@pytest.mark.asyncio
async def test_run_short_list_not_truncated(runner: MCPToolRunner) -> None:
    tr = await runner.run("tc_s", "pydantic_with_long_list", {"n": 5})
    assert tr.truncated is False
    parsed = json.loads(tr.content)
    assert len(parsed["items"]) == 5
