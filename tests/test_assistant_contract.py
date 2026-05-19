"""Shape tests for app/services/assistant/contract.py.

The concrete `DefaultAssistantService` lands in slice 2; until then
we verify the Protocol surface itself: a no-op stub that claims to
implement the Protocol must be accepted by `isinstance` checks, and
the Protocol must expose every method named in plan §4/§8.
"""
from __future__ import annotations

from typing import AsyncIterator

import pytest

from app.services.assistant.contract import AssistantService, Principal
from app.services.assistant.schemas import (
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    AssistantStreamEvent,
    ConfirmRequest,
    ContinueRequest,
    Conversation,
    ConversationTurn,
    StreamEventType,
)


# ─────────────────────────────────────────────────────────────────────
# Protocol surface — every method spec'd in the plan exists.
# ─────────────────────────────────────────────────────────────────────


REQUIRED_METHODS = (
    "start_conversation",
    "continue_conversation",
    "confirm_tool_call",
    "cancel",
    "load_conversation",
    "list_conversations",
)


@pytest.mark.parametrize("method", REQUIRED_METHODS)
def test_assistant_service_protocol_has_method(method: str) -> None:
    assert hasattr(AssistantService, method), (
        f"AssistantService Protocol is missing required method {method!r}"
    )


def test_principal_protocol_has_required_attrs() -> None:
    """Plan §6 needs `tenant_id` + `roles` + `plan` to drive tool authz."""
    # Annotated on the Protocol class — read via __annotations__.
    annotations = Principal.__annotations__
    for attr in ("user_id", "tenant_id", "roles", "plan"):
        assert attr in annotations, f"Principal Protocol missing {attr!r}"


# ─────────────────────────────────────────────────────────────────────
# Structural-subtyping check — a duck-typed fake satisfies the Protocol.
# ─────────────────────────────────────────────────────────────────────


class _DevPrincipal:
    """Minimal dev-mode Principal stand-in."""

    user_id = DEFAULT_USER_ID
    tenant_id = DEFAULT_TENANT_ID
    roles = ["owner"]
    plan = "dev"


class _NoopAssistant:
    """A do-nothing stub that nonetheless satisfies the Protocol shape."""

    async def start_conversation(
        self, *, principal: Principal, title: str | None = None
    ) -> Conversation:
        raise NotImplementedError

    async def continue_conversation(
        self,
        *,
        principal: Principal,
        conversation_id: str,
        request: ContinueRequest,
    ) -> AsyncIterator[AssistantStreamEvent]:
        raise NotImplementedError

    async def confirm_tool_call(
        self,
        *,
        principal: Principal,
        conversation_id: str,
        request: ConfirmRequest,
    ) -> AsyncIterator[AssistantStreamEvent]:
        raise NotImplementedError

    async def cancel(self, *, principal: Principal, conversation_id: str) -> None:
        raise NotImplementedError

    async def load_conversation(
        self, *, principal: Principal, conversation_id: str
    ) -> tuple[Conversation, list[ConversationTurn]]:
        raise NotImplementedError

    async def list_conversations(
        self,
        *,
        principal: Principal,
        limit: int = 50,
        include_deleted: bool = False,
    ) -> list[Conversation]:
        raise NotImplementedError


def test_noop_assistant_is_structurally_an_assistant_service() -> None:
    """Structural typing: any object with the right shape is acceptable.

    This is the contract guarantee callers rely on — they accept
    `AssistantService` and we can swap real / fake / null
    implementations without inheritance.
    """
    impl: AssistantService = _NoopAssistant()  # mypy / pyright check
    for method in REQUIRED_METHODS:
        assert callable(getattr(impl, method))


def test_dev_principal_is_structurally_a_principal() -> None:
    principal: Principal = _DevPrincipal()
    assert principal.tenant_id == DEFAULT_TENANT_ID
    assert principal.user_id == DEFAULT_USER_ID
    assert "owner" in principal.roles
    assert principal.plan == "dev"


# ─────────────────────────────────────────────────────────────────────
# Sanity: known protocol event types referenced by the contract docstring.
# ─────────────────────────────────────────────────────────────────────


def test_continue_conversation_terminal_event_types_exist() -> None:
    """The contract promises one of these always terminates the stream."""
    assert hasattr(StreamEventType, "DONE")
    assert hasattr(StreamEventType, "ERROR")
    assert hasattr(StreamEventType, "TURN_COMPLETED")
