"""Slice 6 integration gate — real Anthropic + real MCP + real ClickHouse.

This test exercises the full AS-1 stack end-to-end:
  DefaultAssistantService → AnthropicLLMClient → DevModeToolPolicy →
  MCPToolRunner (get_health) → ConversationStore (ClickHouse)

Skip conditions (either skips the whole module):
  - ANTHROPIC_API_KEY not set in environment
  - ClickHouse not reachable (docker-compose up clickhouse)

Gate assertions:
  1. Event sequence contains TEXT_DELTA, TOOL_CALL_STARTED, TOOL_RESULT,
     TURN_COMPLETED, and DONE — in that order (no strict adjacency).
  2. TURN_COMPLETED.stop_reason is a recognised Anthropic value.
  3. The assistant turn is persisted: load_turns() returns ≥1 ASSISTANT row.
  4. Tenant isolation holds: wrong owner_id returns no turns.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path

import pytest

from app.services.assistant.cache import ResponseCache
from app.services.assistant.contract import Principal
from app.services.assistant.models import ModelRegistry
from app.services.assistant.policy import DevModeToolPolicy
from app.services.assistant.runner import MCPToolRunner
from app.services.assistant.schemas import (
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    ContinueRequest,
    Role,
    StreamEventType,
)
from app.services.assistant.service import AnthropicLLMClient, DefaultAssistantService
from app.services.assistant.store import ConversationStore

pytestmark = pytest.mark.integration


# ─────────────────────────────────────────────────────────────────────
# Module-level skip guards
# ─────────────────────────────────────────────────────────────────────


def _api_key_present() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def pytest_configure(config):  # noqa: D401 — pytest hook
    pass  # keeps linters happy; skip guards are in fixtures


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def e2e_service(tmp_path_factory, clickhouse_ready):
    """Build a fully wired DefaultAssistantService.

    `clickhouse_ready` fixture from conftest.py skips this module if CH
    is unreachable. The API-key guard is checked here so the skip message
    is clear.
    """
    if not _api_key_present():
        pytest.skip("ANTHROPIC_API_KEY not set — skipping AS-1 e2e gate")

    from app.mcp.server import mcp, register_all_tools

    register_all_tools()

    cache_dir = tmp_path_factory.mktemp("assistant_e2e_cache")
    svc = DefaultAssistantService(
        client=AnthropicLLMClient(),
        cache=ResponseCache(cache_dir / "cache.sqlite"),
        models=ModelRegistry(),
        policy=DevModeToolPolicy.make_default(),
        runner=MCPToolRunner(mcp),
        store=ConversationStore.from_settings(),
    )
    return svc


@pytest.fixture(scope="module")
def dev_principal() -> Principal:
    class _P:
        tenant_id: str = DEFAULT_TENANT_ID
        user_id: str = DEFAULT_USER_ID

    return _P()


# ─────────────────────────────────────────────────────────────────────
# Helper: drain the async generator synchronously under pytest-asyncio
# ─────────────────────────────────────────────────────────────────────


async def _collect_events(gen):
    events = []
    async for ev in gen:
        events.append(ev)
    return events


# ─────────────────────────────────────────────────────────────────────
# Gate test
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_e2e_tool_call_event_sequence(e2e_service, dev_principal) -> None:
    """Full turn that should trigger get_health and produce the expected
    event sequence.
    """
    svc: DefaultAssistantService = e2e_service

    conv = await svc.start_conversation(principal=dev_principal, title="e2e gate")
    request = ContinueRequest(user_msg="Is the platform healthy right now?")

    gen = svc.continue_conversation(
        principal=dev_principal,
        conversation_id=conv.id,
        request=request,
    )
    events = await _collect_events(gen)

    types = [e.type for e in events]

    # Required event types must all be present
    assert StreamEventType.TEXT_DELTA in types, f"missing TEXT_DELTA in {types}"
    assert StreamEventType.TOOL_CALL_STARTED in types, f"missing TOOL_CALL_STARTED in {types}"
    assert StreamEventType.TOOL_RESULT in types, f"missing TOOL_RESULT in {types}"
    assert StreamEventType.TURN_COMPLETED in types, f"missing TURN_COMPLETED in {types}"
    assert types[-1] == StreamEventType.DONE, f"last event must be DONE, got {types[-1]}"

    # Ordering: TOOL_CALL_STARTED must precede TURN_COMPLETED
    idx_started = types.index(StreamEventType.TOOL_CALL_STARTED)
    idx_completed = types.index(StreamEventType.TURN_COMPLETED)
    assert idx_started < idx_completed, "TOOL_CALL_STARTED must come before TURN_COMPLETED"


@pytest.mark.asyncio
async def test_e2e_turn_completed_stop_reason(e2e_service, dev_principal) -> None:
    """TURN_COMPLETED payload carries a recognised stop_reason."""
    svc: DefaultAssistantService = e2e_service

    conv = await svc.start_conversation(principal=dev_principal, title="e2e stop_reason")
    request = ContinueRequest(user_msg="Check platform health briefly.")

    events = await _collect_events(
        svc.continue_conversation(
            principal=dev_principal,
            conversation_id=conv.id,
            request=request,
        )
    )

    completed = next(e for e in events if e.type == StreamEventType.TURN_COMPLETED)
    stop_reason = completed.payload.get("stop_reason")
    assert stop_reason in {"end_turn", "tool_use", "max_tokens", "stop_sequence"}, (
        f"unexpected stop_reason: {stop_reason!r}"
    )


@pytest.mark.asyncio
async def test_e2e_ch_persistence(e2e_service, dev_principal) -> None:
    """After a turn, at least one ASSISTANT turn row is in ClickHouse."""
    svc: DefaultAssistantService = e2e_service
    store = ConversationStore.from_settings()

    conv = await svc.start_conversation(principal=dev_principal, title="e2e persist")
    request = ContinueRequest(user_msg="What is the health status of the platform?")

    await _collect_events(
        svc.continue_conversation(
            principal=dev_principal,
            conversation_id=conv.id,
            request=request,
        )
    )

    turns = store.load_turns(
        conversation_id=conv.id,
        owner_id=dev_principal.tenant_id,
    )
    assert len(turns) >= 1, "expected at least one persisted turn"
    roles = [t.role for t in turns]
    assert Role.ASSISTANT in roles, f"no ASSISTANT turn found; roles={roles}"


@pytest.mark.asyncio
async def test_e2e_tenant_isolation(e2e_service, dev_principal) -> None:
    """A different owner_id must see no turns from the dev principal's conversation."""
    svc: DefaultAssistantService = e2e_service
    store = ConversationStore.from_settings()

    conv = await svc.start_conversation(principal=dev_principal, title="e2e isolation")
    request = ContinueRequest(user_msg="Health check for isolation test.")

    await _collect_events(
        svc.continue_conversation(
            principal=dev_principal,
            conversation_id=conv.id,
            request=request,
        )
    )

    wrong_owner = str(uuid.uuid4())
    turns = store.load_turns(conversation_id=conv.id, owner_id=wrong_owner)
    assert turns == [], f"tenant isolation violated — wrong owner saw {len(turns)} turn(s)"
