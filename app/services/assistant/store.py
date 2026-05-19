"""ClickHouse-backed persistence for the assistant service.

`ConversationStore` owns the `assistant_conversations` and `assistant_turns`
tables. It is owner-scoped: every read filters by `owner_id` (tenant ID) so
cross-tenant leaks are impossible at the storage layer, not just the service
layer.

Design notes:
  - Sync CH client — the service calls `asyncio.to_thread()` where needed.
  - ReplacingMergeTree(version) on conversations: we upsert updated_at,
    turn_count, and total_cost_usd by inserting with a higher version.
  - Turns are append-only (MergeTree) ordered by sequence — no updates needed.
  - `tool_calls_json` in turns is a JSON array of ToolCall dicts so the full
    turn history is reconstructable without a second table.
  - All queries use parametrised values ({x:Type}) to prevent injection.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from app.db.client import get_client
from app.services.assistant.schemas import (
    Conversation,
    ConversationTurn,
    Role,
    ToolCall,
    ToolCallStatus,
)

logger = logging.getLogger(__name__)


def _now_version() -> int:
    return time.time_ns() // 1_000_000


def _utc(dt: datetime) -> str:
    """Format datetime for CH insertion."""
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


# ─────────────────────────────────────────────────────────────────────
# ConversationStore
# ─────────────────────────────────────────────────────────────────────


class ConversationStore:
    """Owner-scoped reads/writes for conversation metadata and turns."""

    def __init__(self) -> None:
        pass  # get_client() is called lazily per operation

    @classmethod
    def from_settings(cls) -> "ConversationStore":
        return cls()

    # ── Conversations ─────────────────────────────────────────────────

    def save_conversation(self, conv: Conversation) -> None:
        """Upsert conversation header. Safe to call on create and on update."""
        client = get_client()
        client.insert(
            "assistant_conversations",
            [
                [
                    conv.id,
                    conv.owner_id,
                    conv.user_id,
                    conv.title or "",
                    _utc(conv.created_at),
                    _utc(conv.updated_at),
                    conv.turn_count,
                    float(conv.total_cost_usd),
                    _utc(conv.deleted_at) if conv.deleted_at else None,
                    _now_version(),
                ]
            ],
            column_names=[
                "id",
                "owner_id",
                "user_id",
                "title",
                "created_at",
                "updated_at",
                "turn_count",
                "total_cost_usd",
                "deleted_at",
                "version",
            ],
        )
        logger.debug("store.save_conversation id=%s owner=%s", conv.id, conv.owner_id)

    def load_conversation(
        self, *, conversation_id: str, owner_id: str
    ) -> Conversation | None:
        """Return the conversation if it belongs to `owner_id`, else None."""
        client = get_client()
        result = client.query(
            """
            SELECT id, owner_id, user_id, title, created_at, updated_at,
                   turn_count, total_cost_usd, deleted_at
            FROM assistant_conversations FINAL
            WHERE id = {cid:UUID} AND owner_id = {oid:String}
            LIMIT 1
            """,
            parameters={"cid": conversation_id, "oid": owner_id},
        )
        if not result.result_rows:
            return None
        return _row_to_conversation(result.result_rows[0])

    def list_conversations(
        self,
        *,
        owner_id: str,
        limit: int = 50,
        include_deleted: bool = False,
    ) -> list[Conversation]:
        """Return conversations owned by `owner_id`, newest first."""
        client = get_client()
        deleted_filter = "" if include_deleted else "AND deleted_at IS NULL"
        result = client.query(
            f"""
            SELECT id, owner_id, user_id, title, created_at, updated_at,
                   turn_count, total_cost_usd, deleted_at
            FROM assistant_conversations FINAL
            WHERE owner_id = {{oid:String}} {deleted_filter}
            ORDER BY updated_at DESC
            LIMIT {{lim:UInt32}}
            """,
            parameters={"oid": owner_id, "lim": limit},
        )
        return [_row_to_conversation(r) for r in result.result_rows]

    # ── Turns ─────────────────────────────────────────────────────────

    def save_turn(self, turn: ConversationTurn, *, owner_id: str) -> None:
        """Append one turn. Turns are immutable once saved.

        `owner_id` (tenant ID) is stored on the turn row directly to enable
        owner-scoped reads without a join to `assistant_conversations`.
        """
        tool_calls_json = json.dumps(
            [tc.model_dump(mode="json") for tc in turn.tool_calls]
        )
        client = get_client()
        client.insert(
            "assistant_turns",
            [
                [
                    turn.id,
                    turn.conversation_id,
                    owner_id,
                    turn.sequence,
                    str(turn.role),
                    turn.content,
                    tool_calls_json,
                    turn.model or "",
                    turn.tokens_in or 0,
                    turn.tokens_out or 0,
                    float(turn.cost_usd or 0.0),
                    1 if turn.cache_hit else 0,
                    _utc(turn.created_at),
                ]
            ],
            column_names=[
                "id",
                "conversation_id",
                "owner_id",
                "sequence",
                "role",
                "content",
                "tool_calls_json",
                "model",
                "tokens_in",
                "tokens_out",
                "cost_usd",
                "cache_hit",
                "created_at",
            ],
        )
        logger.debug(
            "store.save_turn conv=%s seq=%d role=%s",
            turn.conversation_id,
            turn.sequence,
            turn.role,
        )

    def load_turns(
        self, *, conversation_id: str, owner_id: str
    ) -> list[ConversationTurn]:
        """Return turns for a conversation in sequence order.

        `owner_id` is checked on the turns table directly (no join needed)
        to keep reads fast and tenant-isolated.
        """
        client = get_client()
        result = client.query(
            """
            SELECT id, conversation_id, sequence, role, content,
                   tool_calls_json, model, tokens_in, tokens_out,
                   cost_usd, cache_hit, created_at
            FROM assistant_turns
            WHERE conversation_id = {cid:UUID} AND owner_id = {oid:String}
            ORDER BY sequence ASC
            """,
            parameters={"cid": conversation_id, "oid": owner_id},
        )
        return [_row_to_turn(r) for r in result.result_rows]


# ─────────────────────────────────────────────────────────────────────
# Row mappers
# ─────────────────────────────────────────────────────────────────────


def _row_to_conversation(row: tuple) -> Conversation:
    (
        id_,
        owner_id,
        user_id,
        title,
        created_at,
        updated_at,
        turn_count,
        total_cost_usd,
        deleted_at,
    ) = row
    return Conversation(
        id=str(id_),
        owner_id=owner_id,
        user_id=user_id,
        title=title or None,
        created_at=_ensure_utc(created_at),
        updated_at=_ensure_utc(updated_at),
        turn_count=int(turn_count),
        total_cost_usd=float(total_cost_usd),
        deleted_at=_ensure_utc(deleted_at) if deleted_at else None,
    )


def _row_to_turn(row: tuple) -> ConversationTurn:
    (
        id_,
        conversation_id,
        sequence,
        role,
        content,
        tool_calls_json,
        model,
        tokens_in,
        tokens_out,
        cost_usd,
        cache_hit,
        created_at,
    ) = row
    try:
        tool_call_dicts: list[dict[str, Any]] = json.loads(tool_calls_json or "[]")
        tool_calls = [ToolCall(**tc) for tc in tool_call_dicts]
    except Exception:
        tool_calls = []
    return ConversationTurn(
        id=str(id_),
        conversation_id=str(conversation_id),
        sequence=int(sequence),
        role=Role(role),
        content=content or "",
        tool_calls=tool_calls,
        model=model or None,
        tokens_in=int(tokens_in),
        tokens_out=int(tokens_out),
        cost_usd=float(cost_usd),
        cache_hit=bool(cache_hit),
        created_at=_ensure_utc(created_at),
    )


def _ensure_utc(dt: Any) -> datetime:
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return datetime.fromisoformat(str(dt)).replace(tzinfo=timezone.utc)


__all__ = ["ConversationStore"]
