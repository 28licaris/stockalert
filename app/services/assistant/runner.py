"""Tool dispatcher for the assistant — in-process MCP dispatch + §8.4 truncation.

`ToolRunner` is a Protocol; `MCPToolRunner` is the production implementation.
The runner owns two responsibilities:
  1. `get_tool_defs` — translate allowed tool names → Anthropic-format schemas.
  2. `run` — dispatch one tool call via the in-process FastMCP server.

§8.4 truncation rules (applied before handing content to the LLM):
  - Top-level list values with > 50 items are clipped to 50.
  - The final JSON string is capped at 5 000 characters.
  Both operations set `_truncated=True` in the payload so the model knows.

Why the standard `mcp.tool: <name> ok in X.YYYs` log line appears:
  MCP tool functions wrap their bodies in `with tool_call(name)` from
  `app.mcp.middleware`. Calling a tool via `mcp.call_tool()` invokes that
  function directly, so the middleware fires automatically without any extra
  wrapping in the runner.
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

_MAX_LIST_ITEMS: int = 50
_MAX_STRING_CHARS: int = 5_000


# ─────────────────────────────────────────────────────────────────────
# Result type
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class ToolResult:
    """Result from one assistant-driven tool dispatch.

    `content` is a JSON string (already truncated per §8.4) ready to
    pass verbatim to the Anthropic `tool_result` content block.
    `error` is non-None when the tool raised; in that case `content`
    is `"{}"` and the error message goes to the LLM instead.
    """

    tool_call_id: str
    name: str
    content: str
    error: str | None = None
    truncated: bool = False
    elapsed_s: float = 0.0


# ─────────────────────────────────────────────────────────────────────
# §8.4 truncation
# ─────────────────────────────────────────────────────────────────────


def _truncate(result: dict[str, Any]) -> tuple[str, bool]:
    """Apply §8.4 truncation rules to a tool result dict.

    1. Clip any top-level list value that exceeds _MAX_LIST_ITEMS.
    2. Cap the final JSON string at _MAX_STRING_CHARS characters.
    Returns (json_string, was_truncated).
    """
    truncated = False
    out: dict[str, Any] = {}
    for k, v in result.items():
        if isinstance(v, list) and len(v) > _MAX_LIST_ITEMS:
            out[k] = v[:_MAX_LIST_ITEMS]
            truncated = True
        else:
            out[k] = v

    if truncated:
        out["_truncated"] = True

    text = json.dumps(out, default=str)

    if len(text) > _MAX_STRING_CHARS:
        text = text[:_MAX_STRING_CHARS]
        truncated = True

    return text, truncated


# ─────────────────────────────────────────────────────────────────────
# Protocol + concrete implementation
# ─────────────────────────────────────────────────────────────────────


@runtime_checkable
class ToolRunner(Protocol):
    """Dispatcher boundary — tests substitute a fake; production uses MCPToolRunner."""

    async def get_tool_defs(self, allowed_names: list[str]) -> list[dict[str, Any]]:
        """Anthropic-format tool schemas for the given allowed names."""
        ...

    async def run(
        self, tool_call_id: str, name: str, args: dict[str, Any]
    ) -> ToolResult:
        """Dispatch one tool call and return a typed, truncated result."""
        ...


class MCPToolRunner:
    """Dispatches tool calls via the in-process FastMCP server.

    Injecting the FastMCP instance (rather than importing the global `mcp`)
    keeps this class testable with a local FastMCP that has only test tools.
    Production passes the singleton from `app.mcp.server`.
    """

    def __init__(self, mcp_server: Any) -> None:
        self._mcp = mcp_server

    async def get_tool_defs(self, allowed_names: list[str]) -> list[dict[str, Any]]:
        allowed = set(allowed_names)
        all_tools = await self._mcp.list_tools()
        return [
            {
                "name": t.name,
                "description": t.description or "",
                "input_schema": t.inputSchema,
            }
            for t in all_tools
            if t.name in allowed
        ]

    async def run(
        self, tool_call_id: str, name: str, args: dict[str, Any]
    ) -> ToolResult:
        start = time.monotonic()
        try:
            raw = await self._mcp.call_tool(name, args)
            # FastMCP returns (content_list, result_dict) for typed/Pydantic tools
            # and just content_list for plain-dict-returning tools.
            if isinstance(raw, tuple):
                _, result_dict = raw
            else:
                # Plain dict return: parse the JSON text from the first TextContent.
                text = raw[0].text if raw else "{}"
                try:
                    result_dict = json.loads(text)
                except json.JSONDecodeError:
                    result_dict = {"result": text}

            content, truncated = _truncate(result_dict)
            elapsed = time.monotonic() - start
            return ToolResult(
                tool_call_id=tool_call_id,
                name=name,
                content=content,
                error=None,
                truncated=truncated,
                elapsed_s=elapsed,
            )
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.error(
                "assistant.runner: tool %r failed after %.3fs: %s",
                name,
                elapsed,
                exc,
            )
            return ToolResult(
                tool_call_id=tool_call_id,
                name=name,
                content="{}",
                error=str(exc),
                truncated=False,
                elapsed_s=elapsed,
            )


__all__ = ["MCPToolRunner", "ToolResult", "ToolRunner", "_truncate"]
