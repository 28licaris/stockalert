"""HTTP/SSE routes for the assistant copilot.

Mounted at /cockpit/assistant by main_api.py.

Auth: with ``AUTH_ENABLED=true`` the caller's session Principal is used, so a
conversation is owned by (and only readable by) the caller's tenant, and the
tool set is restricted for non-operators (see ``policy.RoleAwareToolPolicy``).
With ``AUTH_ENABLED=false`` (single-operator local dev) a fixed dev principal
(DEFAULT_TENANT_ID / DEFAULT_USER_ID, treated as operator) is used.

Endpoints:
    POST /cockpit/assistant/conversations
        Start a new conversation. Returns the Conversation JSON.

    GET  /cockpit/assistant/conversations
        List conversations for the dev-mode principal.

    GET  /cockpit/assistant/conversations/{conversation_id}
        Load conversation header + turns.

    POST /cockpit/assistant/conversations/{conversation_id}/turn
        Stream one assistant turn as SSE (text/event-stream).
        Body: ContinueRequest JSON.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from app.api.auth_dependencies import get_principal_or_dev
from app.services.assistant.cache import ResponseCache
from app.services.assistant.contract import Principal
from app.services.assistant.models import ModelRegistry
from app.services.assistant.schemas import (
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    ConfirmRequest,
    ContinueRequest,
)
from app.services.assistant.service import AnthropicLLMClient, DefaultAssistantService
from app.services.assistant.stream import event_stream
from app.services.identity.permissions import OPERATOR_ACCESS

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────
# Principal
# ─────────────────────────────────────────────────────────────────────


class _DevPrincipal:
    """Local single-operator principal (AUTH_ENABLED=false only)."""

    user_id: str = DEFAULT_USER_ID
    tenant_id: str = DEFAULT_TENANT_ID
    roles: tuple[str, ...] = ("owner",)
    plan: str = "dev"
    is_operator: bool = True


_DEV_PRINCIPAL: Principal = _DevPrincipal()  # type: ignore[assignment]


class _SessionPrincipal:
    """Adapter from the identity ``Principal`` to the assistant's protocol.

    ``tenant_id`` is the conversation ``owner_id``: every store read filters on
    it, so one tenant can never load another's conversations.
    """

    def __init__(self, identity_principal) -> None:  # noqa: ANN001
        self.user_id = str(identity_principal.user_id)
        self.tenant_id = str(identity_principal.tenant_id)
        self.roles = tuple(sorted(r.value for r in identity_principal.roles))
        self.plan = "customer"
        self.is_operator = OPERATOR_ACCESS in identity_principal.permissions


def get_assistant_principal(request: Request) -> Principal:
    """Resolve who is calling the assistant (401 without a session when auth is on)."""
    from app.config import settings

    if not settings.auth_enabled:
        return _DEV_PRINCIPAL
    return _SessionPrincipal(get_principal_or_dev(request))  # type: ignore[return-value]


# ─────────────────────────────────────────────────────────────────────
# Service singleton (lazy; one instance per process)
# ─────────────────────────────────────────────────────────────────────


@lru_cache(maxsize=1)
def _get_service() -> DefaultAssistantService:
    from app.mcp.server import mcp, register_all_tools
    from app.services.assistant.policy import DevModeToolPolicy, RoleAwareToolPolicy
    from app.services.assistant.runner import MCPToolRunner
    from app.services.assistant.store import ConversationStore

    register_all_tools()
    return DefaultAssistantService(
        client=AnthropicLLMClient(),
        cache=ResponseCache(Path(".cache") / "assistant_responses.sqlite"),
        models=ModelRegistry(),
        policy=RoleAwareToolPolicy(DevModeToolPolicy.make_default()),
        runner=MCPToolRunner(mcp),
        store=ConversationStore.from_settings(),
    )


# ─────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────


@router.post("/conversations", tags=["Assistant"])
async def start_conversation(
    title: str | None = None, principal: Principal = Depends(get_assistant_principal)
):
    """Start a new conversation and return its metadata."""
    svc = _get_service()
    conv = await svc.start_conversation(principal=principal, title=title)
    return conv.model_dump(mode="json")


@router.get("/conversations", tags=["Assistant"])
async def list_conversations(
    limit: int = 50,
    include_deleted: bool = False,
    principal: Principal = Depends(get_assistant_principal),
):
    """List conversations owned by the current principal."""
    svc = _get_service()
    convs = await svc.list_conversations(
        principal=principal,
        limit=limit,
        include_deleted=include_deleted,
    )
    return [c.model_dump(mode="json") for c in convs]


@router.get("/conversations/{conversation_id}", tags=["Assistant"])
async def load_conversation(
    conversation_id: str, principal: Principal = Depends(get_assistant_principal)
):
    """Load conversation header and full turn history."""
    svc = _get_service()
    try:
        conv, turns = await svc.load_conversation(
            principal=principal,
            conversation_id=conversation_id,
        )
    except KeyError:
        raise HTTPException(status_code=404, detail="conversation not found")
    return {
        "conversation": conv.model_dump(mode="json"),
        "turns": [t.model_dump(mode="json") for t in turns],
    }


@router.post("/conversations/{conversation_id}/turn", tags=["Assistant"])
async def continue_conversation(
    conversation_id: str,
    request: ContinueRequest,
    principal: Principal = Depends(get_assistant_principal),
):
    """Stream one assistant turn as SSE.

    Response is `text/event-stream`. Each SSE event is:
        data: {"type": "<StreamEventType>", "payload": {...}}

    The stream ends when the client receives a `done` event.
    """
    svc = _get_service()
    events = svc.continue_conversation(
        principal=principal,
        conversation_id=conversation_id,
        request=request,
    )
    return StreamingResponse(
        event_stream(events),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # disable nginx buffering for SSE
        },
    )


@router.post("/conversations/{conversation_id}/confirm", tags=["Assistant"])
async def confirm_tool_call(
    conversation_id: str,
    request: ConfirmRequest,
    principal: Principal = Depends(get_assistant_principal),
):
    """Confirm or deny a pending write-tool call (wired in AS-2)."""
    svc = _get_service()
    events = svc.confirm_tool_call(
        principal=principal,
        conversation_id=conversation_id,
        request=request,
    )
    try:
        async for _ in events:
            pass
    except NotImplementedError:
        raise HTTPException(
            status_code=501,
            detail="Confirm/deny for write tools lands in AS-2.",
        )


@router.delete("/conversations/{conversation_id}", tags=["Assistant"])
async def cancel_conversation(
    conversation_id: str, principal: Principal = Depends(get_assistant_principal)
):
    """Cancel any in-flight turn for this conversation."""
    svc = _get_service()
    await svc.cancel(principal=principal, conversation_id=conversation_id)
    return {"cancelled": True}
