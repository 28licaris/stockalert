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


# Tools a non-operator (customer) may use. DEFAULT-DENY: a tool absent from this
# set is invisible to customers. Mirrors the customer REST surface
# (app/api/auth_middleware.py CUSTOMER_RULES): market data, options hot tier,
# Elliott Wave, news, instruments. Lake/ops/health tools, the shared (operator)
# watchlists, shared backtest registry, universe screener and the
# sector-theme writers stay operator-only.
CUSTOMER_TOOLS: frozenset[str] = frozenset(
    {
        "compute_indicator",
        "compute_indicators",
        "evaluate_wave_targets",
        "get_bars_for_chart",
        "get_bars_in_range",
        "get_chart_data",
        "get_economic_data",
        "get_instruments",
        "get_latest_bar_per_symbol",
        "get_latest_option_contracts",
        "get_latest_option_gamma_exposure",
        "get_market_hours",
        "get_news",
        "get_option_gamma_exposure",
        "get_price_stats",
        "get_quote",
        "get_quotes",
        "get_recent_bars",
        "get_recent_signals",
        "get_signals_by_symbol",
        "get_wave_state",
        "list_sector_themes",
        "list_wave_alerts",
        "scan_intraday_wave_alerts",
        "search_instrument",
    }
)

# Explicitly operator-only (classification is checked by a test so a newly
# registered MCP tool cannot silently appear in neither set).
OPERATOR_TOOLS: frozenset[str] = frozenset(
    {
        "create_sector_theme",
        "delete_sector_theme",
        "find_intraday_gaps",
        "get_adjusted_bars",
        "get_bronze_bars",
        "get_bronze_table_stats",
        "get_corp_actions",
        "get_coverage",
        "get_cross_provider_diff",
        "get_health",
        "get_lake_freshness",
        "get_latest_trading_day",
        "get_movers",
        "get_option_contracts",
        "get_symbol_coverage",
        "get_watchlist",
        "get_watchlist_members",
        "list_adjusted_symbols",
        "list_bronze_symbols",
        "list_lake_snapshots",
        "list_strategy_runs",
        "list_watchlists",
        "run_backtest",
        "scan_universe",
    }
)


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


class RoleAwareToolPolicy:
    """Production policy: operators get ``inner``'s tools; everyone else gets
    only the :data:`CUSTOMER_TOOLS` subset of them.

    "Operator" is read from ``principal.is_operator`` (set by
    ``routes_assistant`` from the ``operator.access`` permission). A principal
    without the attribute is treated as a customer — fail closed.
    """

    def __init__(self, inner: ToolPolicy) -> None:
        self._inner = inner

    def allowed_for(self, principal: Principal) -> list[str]:
        allowed = self._inner.allowed_for(principal)
        if getattr(principal, "is_operator", False):
            return allowed
        return [n for n in allowed if n in CUSTOMER_TOOLS]

    def is_write_tool(self, name: str) -> bool:
        return self._inner.is_write_tool(name)


__all__ = [
    "CUSTOMER_TOOLS",
    "OPERATOR_TOOLS",
    "DevModeToolPolicy",
    "RoleAwareToolPolicy",
    "ToolPolicy",
    "WRITE_TOOLS",
]
