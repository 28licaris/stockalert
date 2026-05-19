"""Integration tests for `ConversationStore`.

Requires a live ClickHouse instance (docker-compose up clickhouse).
All tests use unique UUID-based owner_ids to avoid cross-test pollution.

Acceptance criteria (slice 4):
  - save_conversation → load_conversation round-trip.
  - list_conversations is owner-scoped — another tenant never leaks.
  - save_turn → load_turns returns turns in sequence order.
  - Tenant isolation holds on load_turns (wrong owner sees no rows).
  - deleted_at filter: include_deleted=False hides soft-deleted conversations.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from app.services.assistant.schemas import (
    Conversation,
    ConversationTurn,
    Role,
    ToolCallStatus,
)
from app.services.assistant.store import ConversationStore

pytestmark = pytest.mark.integration


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _conv(owner_id: str, **kwargs) -> Conversation:
    now = _now()
    return Conversation(
        id=str(uuid.uuid4()),
        owner_id=owner_id,
        user_id="u1",
        created_at=now,
        updated_at=now,
        **kwargs,
    )


def _turn(
    conversation_id: str,
    sequence: int,
    role: Role = Role.ASSISTANT,
    content: str = "hello",
) -> ConversationTurn:
    return ConversationTurn(
        id=str(uuid.uuid4()),
        conversation_id=conversation_id,
        sequence=sequence,
        role=role,
        content=content,
        created_at=_now(),
    )


# ─────────────────────────────────────────────────────────────────────
# Conversations
# ─────────────────────────────────────────────────────────────────────


def test_save_and_load_conversation_round_trip(clickhouse_ready: bool) -> None:
    store = ConversationStore()
    owner = f"tenant-{uuid.uuid4()}"
    conv = _conv(owner, title="Test conv")
    store.save_conversation(conv)

    loaded = store.load_conversation(conversation_id=conv.id, owner_id=owner)
    assert loaded is not None
    assert loaded.id == conv.id
    assert loaded.owner_id == owner
    assert loaded.title == "Test conv"


def test_load_conversation_wrong_owner_returns_none(clickhouse_ready: bool) -> None:
    store = ConversationStore()
    owner = f"tenant-{uuid.uuid4()}"
    other = f"tenant-{uuid.uuid4()}"
    conv = _conv(owner)
    store.save_conversation(conv)

    result = store.load_conversation(conversation_id=conv.id, owner_id=other)
    assert result is None


def test_load_conversation_missing_returns_none(clickhouse_ready: bool) -> None:
    store = ConversationStore()
    result = store.load_conversation(
        conversation_id=str(uuid.uuid4()), owner_id="some-tenant"
    )
    assert result is None


def test_list_conversations_owner_scoped(clickhouse_ready: bool) -> None:
    store = ConversationStore()
    owner_a = f"tenant-{uuid.uuid4()}"
    owner_b = f"tenant-{uuid.uuid4()}"

    conv_a = _conv(owner_a)
    conv_b = _conv(owner_b)
    store.save_conversation(conv_a)
    store.save_conversation(conv_b)

    results_a = store.list_conversations(owner_id=owner_a)
    ids_a = [c.id for c in results_a]
    assert conv_a.id in ids_a
    assert conv_b.id not in ids_a


def test_list_conversations_excludes_deleted_by_default(clickhouse_ready: bool) -> None:
    store = ConversationStore()
    owner = f"tenant-{uuid.uuid4()}"

    active = _conv(owner, title="active")
    deleted = _conv(owner, title="deleted", deleted_at=_now())
    store.save_conversation(active)
    store.save_conversation(deleted)

    results = store.list_conversations(owner_id=owner)
    ids = [c.id for c in results]
    assert active.id in ids
    assert deleted.id not in ids


def test_list_conversations_include_deleted(clickhouse_ready: bool) -> None:
    store = ConversationStore()
    owner = f"tenant-{uuid.uuid4()}"

    deleted = _conv(owner, deleted_at=_now())
    store.save_conversation(deleted)

    results = store.list_conversations(owner_id=owner, include_deleted=True)
    assert any(c.id == deleted.id for c in results)


def test_upsert_conversation_updates_fields(clickhouse_ready: bool) -> None:
    """Saving with a higher version should win (ReplacingMergeTree)."""
    store = ConversationStore()
    owner = f"tenant-{uuid.uuid4()}"
    conv = _conv(owner, turn_count=0)
    store.save_conversation(conv)

    updated = conv.model_copy(update={"turn_count": 5, "total_cost_usd": 0.05})
    store.save_conversation(updated)

    # Give CH ReplacingMergeTree time to merge (or use FINAL in the query — it does)
    loaded = store.load_conversation(conversation_id=conv.id, owner_id=owner)
    assert loaded is not None
    assert loaded.turn_count == 5
    assert abs(loaded.total_cost_usd - 0.05) < 1e-6


# ─────────────────────────────────────────────────────────────────────
# Turns
# ─────────────────────────────────────────────────────────────────────


def test_save_and_load_turns_round_trip(clickhouse_ready: bool) -> None:
    store = ConversationStore()
    owner = f"tenant-{uuid.uuid4()}"
    conv = _conv(owner)
    store.save_conversation(conv)

    t1 = _turn(conv.id, sequence=0, role=Role.USER, content="hello")
    t2 = _turn(conv.id, sequence=1, role=Role.ASSISTANT, content="world")
    store.save_turn(t1, owner_id=owner)
    store.save_turn(t2, owner_id=owner)

    turns = store.load_turns(conversation_id=conv.id, owner_id=owner)
    assert len(turns) == 2
    assert turns[0].sequence == 0
    assert turns[0].role == Role.USER
    assert turns[0].content == "hello"
    assert turns[1].sequence == 1
    assert turns[1].content == "world"


def test_load_turns_ordered_by_sequence(clickhouse_ready: bool) -> None:
    store = ConversationStore()
    owner = f"tenant-{uuid.uuid4()}"
    conv = _conv(owner)
    store.save_conversation(conv)

    # Insert out of order
    for seq in [2, 0, 1]:
        store.save_turn(_turn(conv.id, sequence=seq), owner_id=owner)

    turns = store.load_turns(conversation_id=conv.id, owner_id=owner)
    assert [t.sequence for t in turns] == [0, 1, 2]


def test_load_turns_tenant_isolated(clickhouse_ready: bool) -> None:
    store = ConversationStore()
    owner_a = f"tenant-{uuid.uuid4()}"
    owner_b = f"tenant-{uuid.uuid4()}"
    conv = _conv(owner_a)
    store.save_conversation(conv)

    store.save_turn(_turn(conv.id, sequence=0), owner_id=owner_a)

    # owner_b queries the same conversation_id but a different owner_id
    turns = store.load_turns(conversation_id=conv.id, owner_id=owner_b)
    assert turns == []


def test_load_turns_empty_for_unknown_conversation(clickhouse_ready: bool) -> None:
    store = ConversationStore()
    turns = store.load_turns(
        conversation_id=str(uuid.uuid4()), owner_id="any-tenant"
    )
    assert turns == []


def test_turn_preserves_model_and_tokens(clickhouse_ready: bool) -> None:
    store = ConversationStore()
    owner = f"tenant-{uuid.uuid4()}"
    conv = _conv(owner)
    store.save_conversation(conv)

    turn = ConversationTurn(
        id=str(uuid.uuid4()),
        conversation_id=conv.id,
        sequence=0,
        role=Role.ASSISTANT,
        content="answer",
        model="claude-sonnet-4-6",
        tokens_in=100,
        tokens_out=42,
        cost_usd=0.001,
        cache_hit=False,
        created_at=_now(),
    )
    store.save_turn(turn, owner_id=owner)

    turns = store.load_turns(conversation_id=conv.id, owner_id=owner)
    assert len(turns) == 1
    t = turns[0]
    assert t.model == "claude-sonnet-4-6"
    assert t.tokens_in == 100
    assert t.tokens_out == 42
    assert abs(t.cost_usd - 0.001) < 1e-6
