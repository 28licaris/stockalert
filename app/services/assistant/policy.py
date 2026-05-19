"""Tool access policy for the assistant copilot.

`ToolPolicy` is a Protocol — tests and future SaaS tiers substitute without
subclassing. `DevModeToolPolicy` is the AS-1 production instance: grants
read-only access to all registered MCP tools; blocks write tools.

Write tools (AS-2 confirm-before-mutate list):
  - run_backtest  ← first write tool; confirm-before-mutate lands in AS-2
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from app.services.assistant.contract import Principal

# Single source of truth for "is this a write tool" — referenced in
# AS-2's confirm-before-mutate flow and in the tests.
WRITE_TOOLS: frozenset[str] = frozenset({"run_backtest"})


@runtime_checkable
class ToolPolicy(Protocol):
    """Decides which tools a principal may see and invoke."""

    def allowed_for(self, principal: Principal) -> list[str]:
        """Ordered list of tool names visible to this principal.

        Denied tools NEVER appear in the `tools=` prompt payload;
        they are not shown as disabled — they simply do not exist.
        """
        ...

    def is_write_tool(self, name: str) -> bool:
        """True iff the tool mutates state (requires confirm-before-mutate in AS-2)."""
        ...


class DevModeToolPolicy:
    """All registered read-only MCP tools allowed; write tools blocked.

    Constructed with an explicit `all_tool_names` list so tests can work
    without the real MCP server. Use `make_default()` in production.

    SaaS note: future `TenantToolPolicy` will filter by subscription tier
    before applying the write-tool block. The Protocol seam is already here.
    """

    def __init__(self, all_tool_names: list[str]) -> None:
        self._all_names = list(all_tool_names)

    @classmethod
    def make_default(cls) -> "DevModeToolPolicy":
        """Production factory — reads names from the global MCP registry."""
        from app.mcp.server import mcp, register_all_tools  # lazy import

        register_all_tools()
        # _tool_manager.list_tools() is sync (the async FastMCP wrapper is
        # protocol compliance only). We call it at construction time and
        # freeze the snapshot — tool registration happens once at startup.
        tools = mcp._tool_manager.list_tools()
        return cls([t.name for t in tools])

    def allowed_for(self, principal: Principal) -> list[str]:
        return [n for n in self._all_names if n not in WRITE_TOOLS]

    def is_write_tool(self, name: str) -> bool:
        return name in WRITE_TOOLS


__all__ = ["DevModeToolPolicy", "ToolPolicy", "WRITE_TOOLS"]
