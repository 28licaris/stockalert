"""Assistant access control for a multi-user deployment.

Three guarantees, all of which were absent when the assistant ran as one
hard-coded dev principal:

1. **Tool classification is explicit.** Every registered MCP tool is either in
   ``CUSTOMER_TOOLS`` or ``OPERATOR_TOOLS`` (never both, none unclassified), so a
   newly added tool cannot silently become available to every signed-in user.
2. **Customers only get customer tools** — both in the prompt and, because the
   model can emit any tool name, at execution time.
3. **Conversations belong to the caller's tenant.** The routes use the session
   Principal when auth is on; no session is a 401.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import auth_dependencies
from app.config import settings
from app.services.assistant.cache import ResponseCache
from app.services.assistant.models import ModelRegistry
from app.services.assistant.policy import (
    CUSTOMER_TOOLS,
    OPERATOR_TOOLS,
    WRITE_TOOLS,
    DevModeToolPolicy,
    RoleAwareToolPolicy,
)
from app.services.assistant.runner import ToolResult
from app.services.assistant.schemas import ContinueRequest, StreamEventType
from app.services.assistant.service import (
    DefaultAssistantService,
    LLMResult,
    LLMToolUse,
    LLMUsage,
)
from app.services.identity.schemas import Principal, Role


@dataclass
class _Who:
    user_id: str = "u1"
    tenant_id: str = "t1"
    roles: tuple[str, ...] = ("owner",)
    plan: str = "customer"
    is_operator: bool = False


# ── 1. classification ─────────────────────────────────────────────────


def _registered_tools() -> set[str]:
    from app.mcp.server import mcp, register_all_tools

    register_all_tools()
    return {t.name for t in mcp._tool_manager.list_tools()}


def test_every_registered_mcp_tool_is_classified() -> None:
    registered = _registered_tools()
    unclassified = registered - CUSTOMER_TOOLS - OPERATOR_TOOLS
    assert not unclassified, (
        f"MCP tool(s) {sorted(unclassified)} are in neither CUSTOMER_TOOLS nor "
        "OPERATOR_TOOLS (app/services/assistant/policy.py). Decide who may use them."
    )


def test_tool_sets_are_disjoint_and_not_stale() -> None:
    registered = _registered_tools()
    assert not (CUSTOMER_TOOLS & OPERATOR_TOOLS)
    assert not (CUSTOMER_TOOLS | OPERATOR_TOOLS) - registered, "classified tool no longer registered"
    assert WRITE_TOOLS <= OPERATOR_TOOLS, "write tools must never be customer tools"


def test_customer_tools_exclude_shared_state_and_ops() -> None:
    # The audit findings: shared watchlists, sector-theme writers, lake/ops tools.
    for name in ("list_watchlists", "get_watchlist", "create_sector_theme", "delete_sector_theme",
                 "get_health", "list_strategy_runs", "run_backtest", "scan_universe"):
        assert name not in CUSTOMER_TOOLS, name


# ── 2. policy ─────────────────────────────────────────────────────────


def test_operator_keeps_every_tool_except_write_tools() -> None:
    names = sorted(_registered_tools())
    policy = RoleAwareToolPolicy(DevModeToolPolicy(names))
    allowed = policy.allowed_for(_Who(is_operator=True))
    assert allowed == [n for n in names if n not in WRITE_TOOLS]


def test_customer_gets_only_customer_tools() -> None:
    names = sorted(_registered_tools())
    policy = RoleAwareToolPolicy(DevModeToolPolicy(names))
    allowed = policy.allowed_for(_Who())
    assert set(allowed) == CUSTOMER_TOOLS
    assert not set(allowed) & OPERATOR_TOOLS


def test_principal_without_operator_flag_is_a_customer() -> None:
    @dataclass
    class NoFlag:
        user_id: str = "u"
        tenant_id: str = "t"
        roles: tuple[str, ...] = ()
        plan: str = "x"

    policy = RoleAwareToolPolicy(DevModeToolPolicy(["get_quote", "get_health"]))
    assert policy.allowed_for(NoFlag()) == ["get_quote"]


# ── 2b. execution-time enforcement ────────────────────────────────────


class _Stream:
    def __init__(self, chunks: list[str], tool_uses: list[LLMToolUse]) -> None:
        self._chunks, self._tool_uses = chunks, tool_uses

    def __aiter__(self):  # noqa: ANN204
        return self._iter()

    async def _iter(self):  # noqa: ANN202
        for c in self._chunks:
            yield c

    async def result(self) -> LLMResult:
        return LLMResult(
            text="".join(self._chunks),
            tool_uses=self._tool_uses,
            stop_reason="tool_use" if self._tool_uses else "end_turn",
            usage=LLMUsage(tokens_in=10, tokens_out=5),
        )


class _CM:
    def __init__(self, s: _Stream) -> None:
        self._s = s

    async def __aenter__(self) -> _Stream:
        return self._s

    async def __aexit__(self, *a: Any) -> None:
        return None


class _TwoPhaseClient:
    """First call asks for ``tool``; second call answers in text."""

    def __init__(self, tool: str) -> None:
        self._tool, self.n = tool, 0

    def stream(self, **_: Any) -> _CM:
        self.n += 1
        if self.n == 1:
            return _CM(_Stream(["checking "], [LLMToolUse(id="tc1", name=self._tool, args={})]))
        return _CM(_Stream(["done"], []))


class _Runner:
    def __init__(self, names: list[str]) -> None:
        self.names, self.run_calls = names, []

    async def get_tool_defs(self, allowed: list[str]) -> list[dict[str, Any]]:
        return [
            {"name": n, "description": n, "input_schema": {"type": "object", "properties": {}}}
            for n in self.names
            if n in allowed
        ]

    async def run(self, tool_call_id: str, name: str, args: dict[str, Any]) -> ToolResult:
        self.run_calls.append(name)
        return ToolResult(tool_call_id=tool_call_id, name=name, content='{"ok": true}')


async def _run_turn(tmp_path: Path, principal: _Who, tool: str) -> tuple[list[Any], _Runner]:
    names = ["get_quote", "list_watchlists"]
    runner = _Runner(names)
    svc = DefaultAssistantService(
        client=_TwoPhaseClient(tool),
        cache=ResponseCache(tmp_path / "c.sqlite"),
        models=ModelRegistry(),
        policy=RoleAwareToolPolicy(DevModeToolPolicy(names)),
        runner=runner,  # type: ignore[arg-type]
    )
    events = [
        e
        async for e in svc.continue_conversation(
            principal=principal,  # type: ignore[arg-type]
            conversation_id="c",
            request=ContinueRequest(user_msg="hi"),
        )
    ]
    return events, runner


@pytest.mark.asyncio
async def test_customer_cannot_run_an_operator_tool_even_if_the_model_asks(tmp_path: Path) -> None:
    events, runner = await _run_turn(tmp_path, _Who(), "list_watchlists")

    assert runner.run_calls == [], "a denied tool must never reach the runner"
    result = next(e for e in events if e.type == StreamEventType.TOOL_RESULT)
    assert result.payload["name"] == "list_watchlists"
    assert "not available" in (result.payload["error"] or "")


@pytest.mark.asyncio
async def test_customer_can_run_a_customer_tool(tmp_path: Path) -> None:
    _, runner = await _run_turn(tmp_path, _Who(), "get_quote")
    assert runner.run_calls == ["get_quote"]


@pytest.mark.asyncio
async def test_operator_can_run_an_operator_tool(tmp_path: Path) -> None:
    _, runner = await _run_turn(tmp_path, _Who(is_operator=True), "list_watchlists")
    assert runner.run_calls == ["list_watchlists"]


# ── 3. routes: conversations belong to the caller ─────────────────────

SESSION_TOKEN = "tok"


def _principal(*, operator: bool, tenant=None) -> Principal:
    return Principal(
        user_id=uuid4(),
        tenant_id=tenant or uuid4(),
        session_id=uuid4(),
        roles=frozenset({Role.OWNER}),
        permissions=frozenset({"operator.access"}) if operator else frozenset(),
    )


class _Identity:
    def __init__(self, principal: Principal) -> None:
        self.principal = principal

    def authenticate_session(self, token: str) -> Principal | None:
        return self.principal if token == SESSION_TOKEN else None


@pytest.fixture
def auth_on_client(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    svc = DefaultAssistantService(
        client=_TwoPhaseClient("get_quote"),
        cache=ResponseCache(tmp_path / "c.sqlite"),
        models=ModelRegistry(),
    )

    def make(principal: Principal) -> TestClient:
        from app.api.routes_assistant import router

        monkeypatch.setattr(settings, "auth_enabled", True)
        fake = _Identity(principal)
        monkeypatch.setattr(auth_dependencies, "get_identity_service", lambda: fake)
        app = FastAPI()
        app.include_router(router, prefix="/cockpit/assistant")
        client = TestClient(app)
        client.cookies.set(settings.auth_cookie_name, SESSION_TOKEN)
        return client

    with patch("app.api.routes_assistant._get_service", return_value=svc):
        yield make


def test_conversation_is_owned_by_the_session_tenant(auth_on_client) -> None:
    principal = _principal(operator=False)
    client = auth_on_client(principal)

    body = client.post("/cockpit/assistant/conversations").json()

    assert body["owner_id"] == str(principal.tenant_id)
    assert body["user_id"] == str(principal.user_id)
    assert body["owner_id"] != "default-tenant"


def test_no_session_is_401_when_auth_is_on(auth_on_client) -> None:
    client = auth_on_client(_principal(operator=False))
    client.cookies.clear()

    response = client.post("/cockpit/assistant/conversations")

    assert response.status_code == 401


def test_session_principal_adapter_flags_operator() -> None:
    from app.api.routes_assistant import _SessionPrincipal

    assert _SessionPrincipal(_principal(operator=True)).is_operator is True
    assert _SessionPrincipal(_principal(operator=False)).is_operator is False


def test_auth_off_uses_the_local_operator_principal(monkeypatch: pytest.MonkeyPatch) -> None:
    from fastapi import Request

    from app.api.routes_assistant import get_assistant_principal

    monkeypatch.setattr(settings, "auth_enabled", False)
    who = get_assistant_principal(Request({"type": "http", "headers": []}))
    assert who.tenant_id == "default-tenant"
    assert who.is_operator is True  # type: ignore[attr-defined]
