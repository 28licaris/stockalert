"""Public contract for the Assistant service.

Defines `AssistantService` as a `Protocol` so callers depend only on
the interface, not the concrete implementation. The default
implementation lands in `service.py` in AS-1 slice 2; a fake
implementation for unit tests lands in `tests/fakes.py` alongside.

Why a Protocol (and not an ABC):
  - Structural typing — fakes don't need to inherit a base.
  - Keeps the contract import-cheap (no concrete deps).
  - Aligns with `feedback_service_module_design`.
"""
from __future__ import annotations

from typing import AsyncIterator, Protocol

from app.services.assistant.schemas import (
    AssistantStreamEvent,
    ConfirmRequest,
    ContinueRequest,
    Conversation,
    ConversationTurn,
)


class Principal(Protocol):
    """Minimal protocol for the auth principal.

    The full type lives in `app/auth/principal.py` (landing as part of
    the SaaS-readiness contract — see frontend_plan.md §7.2). The
    assistant only needs to know `user_id`, `tenant_id`, and `roles`.

    In dev mode, callers pass a sentinel principal (see
    `schemas.DEFAULT_TENANT_ID` / `DEFAULT_USER_ID`).
    """

    user_id: str
    tenant_id: str
    roles: list[str]
    plan: str


class AssistantService(Protocol):
    """The conversational copilot's public surface.

    Implementations:
      - `service.DefaultAssistantService` (AS-1 slice 2): real
        Anthropic SDK + MCP + ClickHouse.
      - `tests.fakes.FakeAssistantService` (per-test): deterministic,
        no network.

    All conversation reads/writes are scoped by `principal.tenant_id`
    (the `owner_id` column on the storage tables). Callers MUST pass
    the principal; never trust the conversation_id alone.
    """

    async def start_conversation(
        self,
        *,
        principal: Principal,
        title: str | None = None,
    ) -> Conversation:
        """Create a new empty conversation owned by `principal`."""

    async def continue_conversation(
        self,
        *,
        principal: Principal,
        conversation_id: str,
        request: ContinueRequest,
    ) -> AsyncIterator[AssistantStreamEvent]:
        """Append a USER turn and stream the assistant's response.

        Yields events in order:
          - zero or more `TEXT_DELTA` events as the model streams text,
          - `TOOL_CALL_STARTED` / `TOOL_RESULT` pairs for read tools,
          - `TOOL_CALL_PENDING_CONFIRM` for write tools (the iterator
            stops here; the caller resumes via `confirm_tool_call`),
          - exactly one `TURN_COMPLETED` per assistant turn,
          - terminal `DONE` (or `ERROR`).

        Raises only on programmer error (e.g. unknown conversation_id,
        principal denied access). Anthropic/MCP failures surface as
        `ERROR` events in the stream — no silent failures.
        """

    async def confirm_tool_call(
        self,
        *,
        principal: Principal,
        conversation_id: str,
        request: ConfirmRequest,
    ) -> AsyncIterator[AssistantStreamEvent]:
        """Resume a turn paused on a `PENDING_CONFIRM` tool call.

        If `decision="deny"`, the LLM is informed and may continue with
        a different action. If `decision="confirm"`, the tool runs.
        Streams the remainder of the turn.
        """

    async def cancel(
        self,
        *,
        principal: Principal,
        conversation_id: str,
    ) -> None:
        """Tear down any in-flight turn for this conversation.

        Marks running tool calls as `CANCELLED` and persists the
        partial turn so the transcript reflects exactly what happened.
        """

    async def load_conversation(
        self,
        *,
        principal: Principal,
        conversation_id: str,
    ) -> tuple[Conversation, list[ConversationTurn]]:
        """Return the header + full ordered turn list for replay/display."""

    async def list_conversations(
        self,
        *,
        principal: Principal,
        limit: int = 50,
        include_deleted: bool = False,
    ) -> list[Conversation]:
        """Return recent conversations owned by `principal`."""
