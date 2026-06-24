"""Shape tests for app/services/assistant/schemas.py.

These tests pin the wire format. The SSE protocol, the storage
layer, and any HTTP client all build on these contracts; a change
that breaks an assertion here is a wire-protocol change.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.services.assistant.schemas import (
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    AssistantStreamEvent,
    ConfirmRequest,
    ContinueRequest,
    Conversation,
    ConversationTurn,
    Role,
    StreamEventType,
    ToolCall,
    ToolCallStatus,
)


def _now() -> datetime:
    return datetime(2026, 5, 18, 12, 0, 0, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────
# Enums — pin the wire-format string values
# ─────────────────────────────────────────────────────────────────────


def test_role_wire_values() -> None:
    """Every Role enum value is the literal string the wire uses."""
    assert Role.USER == "user"
    assert Role.ASSISTANT == "assistant"
    assert Role.TOOL == "tool"
    assert Role.SYSTEM_NOTICE == "system_notice"


def test_tool_call_status_covers_full_lifecycle() -> None:
    expected = {
        "pending_confirm",
        "running",
        "ok",
        "error",
        "denied",
        "cancelled",
    }
    actual = {s.value for s in ToolCallStatus}
    assert actual == expected


def test_stream_event_type_covers_protocol() -> None:
    """All event types named in plan §11 must exist as enum members."""
    required = {
        "text_delta",
        "tool_call_started",
        "tool_call_pending_confirm",
        "tool_result",
        "tool_error",
        "artifact_ready",
        "thinking_delta",
        "quota_warning",
        "turn_completed",
        "error",
        "done",
    }
    actual = {e.value for e in StreamEventType}
    assert required.issubset(actual)


# ─────────────────────────────────────────────────────────────────────
# ToolCall
# ─────────────────────────────────────────────────────────────────────


def test_tool_call_minimal() -> None:
    tc = ToolCall(id="tc_1", name="get_lake_freshness", status=ToolCallStatus.OK)
    assert tc.args == {}
    assert tc.result is None
    assert tc.artifact_refs == []
    assert tc.truncated is False


def test_tool_call_roundtrips_through_json() -> None:
    """Wire-format round-trip — what we serialize is what we deserialize."""
    tc = ToolCall(
        id="tc_2",
        name="run_backtest",
        args={"symbol": "AAPL", "strategy": "sma_crossover"},
        status=ToolCallStatus.PENDING_CONFIRM,
        result={"equity_curve": [1.0, 1.01]},
        artifact_refs=["art_1"],
        truncated=True,
    )
    rehydrated = ToolCall.model_validate_json(tc.model_dump_json())
    assert rehydrated == tc


# ─────────────────────────────────────────────────────────────────────
# ConversationTurn
# ─────────────────────────────────────────────────────────────────────


def test_conversation_turn_user_minimal() -> None:
    turn = ConversationTurn(
        id="turn_1",
        conversation_id="conv_1",
        sequence=0,
        role=Role.USER,
        content="what's the lake freshness?",
        created_at=_now(),
    )
    assert turn.tool_calls == []
    assert turn.cache_hit is False
    assert turn.model is None  # USER turns have no model


def test_conversation_turn_assistant_with_cost() -> None:
    turn = ConversationTurn(
        id="turn_2",
        conversation_id="conv_1",
        sequence=1,
        role=Role.ASSISTANT,
        content="Bronze is fresh through ...",
        model="claude-sonnet-4-6",
        tokens_in=1234,
        tokens_out=456,
        cost_usd=0.0008,
        cache_hit=True,
        created_at=_now(),
    )
    assert turn.cost_usd == pytest.approx(0.0008)
    assert turn.cache_hit is True


def test_conversation_turn_rejects_negative_sequence() -> None:
    with pytest.raises(ValidationError):
        ConversationTurn(
            id="t",
            conversation_id="c",
            sequence=-1,
            role=Role.USER,
            content="x",
            created_at=_now(),
        )


def test_conversation_turn_rejects_negative_tokens() -> None:
    with pytest.raises(ValidationError):
        ConversationTurn(
            id="t",
            conversation_id="c",
            sequence=0,
            role=Role.ASSISTANT,
            content="x",
            tokens_in=-1,
            created_at=_now(),
        )


def test_conversation_turn_rejects_negative_cost() -> None:
    with pytest.raises(ValidationError):
        ConversationTurn(
            id="t",
            conversation_id="c",
            sequence=0,
            role=Role.ASSISTANT,
            content="x",
            cost_usd=-0.01,
            created_at=_now(),
        )


# ─────────────────────────────────────────────────────────────────────
# Conversation
# ─────────────────────────────────────────────────────────────────────


def test_conversation_defaults() -> None:
    conv = Conversation(
        id="conv_1",
        owner_id=DEFAULT_TENANT_ID,
        user_id=DEFAULT_USER_ID,
        created_at=_now(),
        updated_at=_now(),
    )
    assert conv.turn_count == 0
    assert conv.total_cost_usd == 0.0
    assert conv.title is None
    assert conv.deleted_at is None


def test_conversation_owner_id_required() -> None:
    """SaaS-readiness: every conversation MUST carry an owner_id."""
    with pytest.raises(ValidationError):
        Conversation(  # type: ignore[call-arg]
            id="conv_1",
            user_id=DEFAULT_USER_ID,
            created_at=_now(),
            updated_at=_now(),
        )


# ─────────────────────────────────────────────────────────────────────
# AssistantStreamEvent
# ─────────────────────────────────────────────────────────────────────


def test_stream_event_is_frozen() -> None:
    """Emitted events must be immutable to keep the SSE pipeline safe."""
    event = AssistantStreamEvent(
        type=StreamEventType.TEXT_DELTA, payload={"text": "hi"}
    )
    with pytest.raises(ValidationError):
        event.payload = {"text": "tampered"}  # type: ignore[misc]


def test_stream_event_done_payload_optional() -> None:
    event = AssistantStreamEvent(type=StreamEventType.DONE)
    assert event.payload == {}


def test_stream_event_roundtrip() -> None:
    event = AssistantStreamEvent(
        type=StreamEventType.TOOL_RESULT,
        payload={"id": "tc_1", "result": {"freshness_minutes": 1}},
    )
    rehydrated = AssistantStreamEvent.model_validate_json(event.model_dump_json())
    assert rehydrated == event


# ─────────────────────────────────────────────────────────────────────
# Request bodies
# ─────────────────────────────────────────────────────────────────────


def test_continue_request_minimal() -> None:
    req = ContinueRequest(user_msg="hi")
    assert req.model is None
    assert req.use_extended_thinking is False
    assert req.client_request_id is None


def test_continue_request_rejects_empty_msg() -> None:
    with pytest.raises(ValidationError):
        ContinueRequest(user_msg="")


def test_confirm_request_decision_is_literal() -> None:
    confirm = ConfirmRequest(tool_call_id="tc_1", decision="confirm")
    deny = ConfirmRequest(tool_call_id="tc_1", decision="deny")
    assert confirm.decision == "confirm"
    assert deny.decision == "deny"
    with pytest.raises(ValidationError):
        ConfirmRequest(tool_call_id="tc_1", decision="maybe")  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────
# Dev-mode sentinels
# ─────────────────────────────────────────────────────────────────────


def test_dev_mode_sentinels_are_stable() -> None:
    """SaaS migration relies on these exact literal values for backfill."""
    assert DEFAULT_TENANT_ID == "default-tenant"
    assert DEFAULT_USER_ID == "default-user"
