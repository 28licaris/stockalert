"""Tests for `ToolPolicy` and `DevModeToolPolicy`.

Acceptance criteria (slice 3):
  - Denied tools are not visible to the LLM (never in allowed_for output).
  - `is_write_tool` covers the full AS-2 write-tool list.
  - The `ToolPolicy` Protocol is satisfied by `DevModeToolPolicy`
    structurally (no inheritance required).
"""
from __future__ import annotations

import pytest

from app.services.assistant.policy import WRITE_TOOLS, DevModeToolPolicy, ToolPolicy
from app.services.assistant.schemas import DEFAULT_TENANT_ID, DEFAULT_USER_ID


# ─────────────────────────────────────────────────────────────────────
# Stub principal (identical to the one used in test_assistant_service.py)
# ─────────────────────────────────────────────────────────────────────


class _StubPrincipal:
    user_id: str = DEFAULT_USER_ID
    tenant_id: str = DEFAULT_TENANT_ID
    roles: tuple[str, ...] = ("owner",)
    plan: str = "dev"


_PRINCIPAL = _StubPrincipal()


# ─────────────────────────────────────────────────────────────────────
# Protocol conformance
# ─────────────────────────────────────────────────────────────────────


def test_dev_mode_policy_satisfies_tool_policy_protocol() -> None:
    policy = DevModeToolPolicy(all_tool_names=["get_bars", "run_backtest"])
    assert isinstance(policy, ToolPolicy)


# ─────────────────────────────────────────────────────────────────────
# allowed_for — allowlist enforcement
# ─────────────────────────────────────────────────────────────────────


def test_allowed_for_returns_all_non_write_tools() -> None:
    tools = ["get_bars", "list_symbols", "get_freshness"]
    policy = DevModeToolPolicy(all_tool_names=tools)
    assert policy.allowed_for(_PRINCIPAL) == tools


def test_allowed_for_excludes_write_tools() -> None:
    tools = ["get_bars", "run_backtest", "list_symbols"]
    policy = DevModeToolPolicy(all_tool_names=tools)
    allowed = policy.allowed_for(_PRINCIPAL)
    assert "run_backtest" not in allowed
    assert "get_bars" in allowed
    assert "list_symbols" in allowed


def test_allowed_for_empty_when_all_tools_are_writes() -> None:
    policy = DevModeToolPolicy(all_tool_names=list(WRITE_TOOLS))
    assert policy.allowed_for(_PRINCIPAL) == []


def test_allowed_for_preserves_order() -> None:
    tools = ["z_tool", "a_tool", "m_tool"]
    policy = DevModeToolPolicy(all_tool_names=tools)
    assert policy.allowed_for(_PRINCIPAL) == tools


def test_allowed_for_returns_empty_for_no_tools() -> None:
    policy = DevModeToolPolicy(all_tool_names=[])
    assert policy.allowed_for(_PRINCIPAL) == []


# ─────────────────────────────────────────────────────────────────────
# is_write_tool — AS-2 write-tool registry
# ─────────────────────────────────────────────────────────────────────


def test_run_backtest_is_write_tool() -> None:
    """run_backtest is the first AS-2 write tool — it must be blocked."""
    policy = DevModeToolPolicy(all_tool_names=[])
    assert policy.is_write_tool("run_backtest") is True


def test_read_tools_are_not_write_tools() -> None:
    policy = DevModeToolPolicy(all_tool_names=[])
    read_tools = [
        "get_bronze_bars",
        "list_bronze_symbols",
        "get_latest_trading_day",
        "get_lake_freshness",
        "list_strategy_runs",
        "get_movers",
        "screen_tickers",
    ]
    for name in read_tools:
        assert policy.is_write_tool(name) is False, f"{name} should be read-only"


def test_write_tools_constant_matches_policy() -> None:
    """WRITE_TOOLS module constant and DevModeToolPolicy must agree."""
    policy = DevModeToolPolicy(all_tool_names=list(WRITE_TOOLS))
    for name in WRITE_TOOLS:
        assert policy.is_write_tool(name) is True


def test_unknown_tool_is_not_write_tool() -> None:
    policy = DevModeToolPolicy(all_tool_names=[])
    assert policy.is_write_tool("nonexistent_tool") is False


# ─────────────────────────────────────────────────────────────────────
# Constructor defensiveness
# ─────────────────────────────────────────────────────────────────────


def test_constructor_copies_list() -> None:
    """Mutating the original list after construction must not affect the policy."""
    original = ["tool_a", "tool_b"]
    policy = DevModeToolPolicy(all_tool_names=original)
    original.append("tool_c")
    assert "tool_c" not in policy.allowed_for(_PRINCIPAL)
