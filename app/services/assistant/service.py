"""Default implementation of `AssistantService`.

Owns the LLM turn loop: build the prompt, look up the response cache,
stream from Anthropic, emit SSE events. Persistence (`ConversationStore`)
and tool dispatch (`ToolRunner`) are injected as optional deps —
slice 2 ships with an in-memory store stub and no runner; slices 3
and 4 plug in the real implementations without touching this file.

Why a frozen architecture from slice 2: every slice that comes after
adds *behind* a protocol seam this file already accepts. The HTTP/SSE
layer (slice 5) and the integration gate test (slice 6) both program
against `AssistantService`, never against `DefaultAssistantService`.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from app.services.assistant import prompts
from app.services.assistant.cache import CacheKeyInputs, CachedResponse, ResponseCache
from app.services.assistant.contract import Principal
from app.services.assistant.models import ModelChoice, ModelRegistry
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
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# LLM client surface — minimal Protocol the service depends on.
#
# Default impl wraps anthropic.AsyncAnthropic; tests substitute a fake
# without touching the network. Keeps service.py provider-agnostic at
# the boundary without inventing a multi-provider framework upstack.
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class LLMUsage:
    """Token + cache accounting for one LLM call."""

    tokens_in: int
    tokens_out: int
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass(frozen=True, slots=True)
class LLMToolUse:
    """One tool_use block the model emitted."""

    id: str
    name: str
    args: dict[str, Any]


@dataclass(frozen=True, slots=True)
class LLMResult:
    """Result of one completed LLM call.

    `text` is the assembled output; `tool_uses` are any tool_use blocks
    that appeared. `stop_reason` is the model's stop signal (mirrors
    Anthropic's `stop_reason` values: `end_turn`, `tool_use`,
    `max_tokens`, `stop_sequence`).
    """

    text: str
    tool_uses: list[LLMToolUse]
    stop_reason: str
    usage: LLMUsage


@runtime_checkable
class LLMStream(Protocol):
    """One in-flight streamed LLM call.

    Iterating yields plain string text deltas. After the iterator
    drains, call `result()` for the final accounting + any tool_use
    blocks.
    """

    def __aiter__(self) -> AsyncIterator[str]: ...
    async def result(self) -> LLMResult: ...


@runtime_checkable
class LLMClient(Protocol):
    """Provider boundary. `stream` is an async context manager."""

    def stream(
        self,
        *,
        model: str,
        system_blocks: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        thinking_budget: int | None,
    ) -> "AsyncContextManagerOf[LLMStream]": ...


class AsyncContextManagerOf(Protocol):
    """Local shorthand alias — fake mypy stand-in for AsyncContextManager."""

    async def __aenter__(self) -> LLMStream: ...
    async def __aexit__(self, *args: Any) -> None: ...


# ─────────────────────────────────────────────────────────────────────
# Default LLM client — wraps the Anthropic AsyncAnthropic SDK.
# ─────────────────────────────────────────────────────────────────────


class AnthropicLLMClient:
    """Production LLM client. Lazy-imports `anthropic` so the assistant
    package stays cheap to import for callers that don't need the SDK
    (e.g. when reading the schema module only).
    """

    def __init__(self, *, api_key: str | None = None) -> None:
        from anthropic import AsyncAnthropic  # lazy

        # `api_key=None` → SDK reads ANTHROPIC_API_KEY from the env
        # (same pattern as the trading LLMAgent).
        self._client = AsyncAnthropic(api_key=api_key) if api_key else AsyncAnthropic()

    def stream(
        self,
        *,
        model: str,
        system_blocks: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        thinking_budget: int | None,
    ) -> "_AnthropicStreamCM":
        return _AnthropicStreamCM(
            client=self._client,
            model=model,
            system_blocks=system_blocks,
            tools=tools,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking_budget=thinking_budget,
        )


class _AnthropicStreamCM:
    """Bridges `anthropic.AsyncMessageStreamManager` to our `LLMStream` Protocol."""

    def __init__(
        self,
        *,
        client: Any,
        model: str,
        system_blocks: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        messages: list[dict[str, Any]],
        max_tokens: int,
        temperature: float,
        thinking_budget: int | None,
    ) -> None:
        kwargs: dict[str, Any] = {
            "model": model,
            "system": system_blocks,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if tools:
            kwargs["tools"] = tools
        if thinking_budget is not None:
            kwargs["thinking"] = {
                "type": "enabled",
                "budget_tokens": thinking_budget,
            }
        self._mgr = client.messages.stream(**kwargs)
        self._stream: Any = None

    async def __aenter__(self) -> "_AnthropicStreamAdapter":
        self._stream = await self._mgr.__aenter__()
        return _AnthropicStreamAdapter(self._stream)

    async def __aexit__(self, *args: Any) -> None:
        await self._mgr.__aexit__(*args)


class _AnthropicStreamAdapter:
    """Adapts `AsyncMessageStream` → our `LLMStream` Protocol shape."""

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def __aiter__(self) -> AsyncIterator[str]:
        return self._stream.text_stream.__aiter__()

    async def result(self) -> LLMResult:
        final = await self._stream.get_final_message()
        text_parts: list[str] = []
        tool_uses: list[LLMToolUse] = []
        for block in final.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(getattr(block, "text", ""))
            elif btype == "tool_use":
                tool_uses.append(
                    LLMToolUse(
                        id=block.id,
                        name=block.name,
                        args=dict(block.input or {}),
                    )
                )
            # `thinking` blocks (extended-thinking) are silently passed
            # over in slice 2; AS-6 turns them into THINKING_DELTA events.
        usage = final.usage
        return LLMResult(
            text="".join(text_parts),
            tool_uses=tool_uses,
            stop_reason=final.stop_reason or "end_turn",
            usage=LLMUsage(
                tokens_in=getattr(usage, "input_tokens", 0),
                tokens_out=getattr(usage, "output_tokens", 0),
                cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            ),
        )


# ─────────────────────────────────────────────────────────────────────
# DefaultAssistantService — the LLM turn loop.
# ─────────────────────────────────────────────────────────────────────


# Approximate per-1k-token costs for cost accounting on each turn.
# These are *display* numbers (the bill is whatever Anthropic charges);
# we keep them in one place so the UI footer can stay consistent.
# Bump when pricing changes.
_PRICING_PER_1K: dict[str, tuple[float, float]] = {
    # model_id: (input_per_1k_usd, output_per_1k_usd)
    "claude-sonnet-4-6": (0.003, 0.015),
    "claude-opus-4-7": (0.015, 0.075),
}


def _estimate_cost_usd(model: str, usage: LLMUsage) -> float:
    pricing = _PRICING_PER_1K.get(model)
    if pricing is None:
        return 0.0
    in_rate, out_rate = pricing
    # Cache-read input tokens are billed at a discount but we don't
    # have an authoritative public multiplier here; approximate as
    # the same rate. Tweak if/when we want a more precise estimate.
    return (usage.tokens_in / 1000.0) * in_rate + (usage.tokens_out / 1000.0) * out_rate


class DefaultAssistantService:
    """Concrete `AssistantService` for slice 2.

    Implements the read-only, text-streaming happy path of
    `continue_conversation`. Tool dispatch raises `NotImplementedError`
    (slice 3 wires it). Persistence + history is stubbed in-memory
    (slice 4 swaps in the ClickHouse-backed `ConversationStore`).

    Constructor takes everything as injected dependencies so tests
    can substitute fakes without monkey-patching anything global.
    """

    def __init__(
        self,
        *,
        client: LLMClient,
        cache: ResponseCache,
        models: ModelRegistry,
        prompt: prompts.SystemPrompt | None = None,
    ) -> None:
        self._client = client
        self._cache = cache
        self._models = models
        self._prompt = prompt or prompts.current()
        # In-memory stub for slice 2. Replaced in slice 4.
        self._conv_index: dict[str, Conversation] = {}

    # ─── Public Protocol surface ─────────────────────────────────────

    async def start_conversation(
        self,
        *,
        principal: Principal,
        title: str | None = None,
    ) -> Conversation:
        now = datetime.now(tz=timezone.utc)
        conv = Conversation(
            id=str(uuid.uuid4()),
            owner_id=principal.tenant_id,
            user_id=principal.user_id,
            title=title,
            created_at=now,
            updated_at=now,
        )
        self._conv_index[conv.id] = conv
        return conv

    async def continue_conversation(
        self,
        *,
        principal: Principal,
        conversation_id: str,
        request: ContinueRequest,
    ) -> AsyncIterator[AssistantStreamEvent]:
        """Stream one assistant turn.

        Slice 2 scope:
          - text-only request → text-streaming response
          - cache lookup before network; cache store after
          - tool_use blocks → NotImplementedError (slice 3)
          - any Anthropic exception → ERROR event + DONE (no raise)
        """
        choice = self._models.pick(
            use_extended_thinking=request.use_extended_thinking,
            override_model=request.model,
        )
        messages = [{"role": "user", "content": request.user_msg}]
        tools: list[dict[str, Any]] = []  # slice 3 supplies the MCP tools

        # Cache lookup -----------------------------------------------------------------
        key_inputs = CacheKeyInputs(
            model=choice.model,
            system_prompt_sha256=self._prompt.sha256,
            tool_schema_sha256="",  # no tools in slice 2
            messages=messages,
            tool_results=[],
            use_extended_thinking=request.use_extended_thinking,
        )
        cache_key = key_inputs.compute_key()
        hit = self._cache.lookup(cache_key)

        if hit is not None:
            logger.info(
                "assistant.turn cache hit key=%s… principal=%s",
                cache_key[:12], principal.user_id,
            )
            async for event in self._replay_cached(hit, choice=choice):
                yield event
            return

        # Anthropic stream -------------------------------------------------------------
        system_blocks = self._build_system_blocks()
        try:
            async with self._client.stream(
                model=choice.model,
                system_blocks=system_blocks,
                tools=tools,
                messages=messages,
                max_tokens=choice.max_tokens,
                temperature=choice.temperature,
                thinking_budget=choice.thinking_budget,
            ) as stream:
                text_buf: list[str] = []
                async for chunk in stream:
                    text_buf.append(chunk)
                    yield AssistantStreamEvent(
                        type=StreamEventType.TEXT_DELTA,
                        payload={"text": chunk},
                    )
                result = await stream.result()
        except Exception as exc:  # noqa: BLE001 — surfaced as a stream ERROR
            logger.exception(
                "assistant.turn failed conv=%s principal=%s",
                conversation_id, principal.user_id,
            )
            yield AssistantStreamEvent(
                type=StreamEventType.ERROR,
                payload={
                    "kind": exc.__class__.__name__,
                    "message": str(exc),
                },
            )
            yield AssistantStreamEvent(type=StreamEventType.DONE)
            return

        # Tool dispatch is slice 3. If the model asked for tools today,
        # surface it as a structured error rather than silently dropping.
        if result.tool_uses:
            logger.error(
                "assistant.turn received %d tool_use block(s) but ToolRunner "
                "is not wired yet (slice 3). conv=%s",
                len(result.tool_uses), conversation_id,
            )
            yield AssistantStreamEvent(
                type=StreamEventType.ERROR,
                payload={
                    "kind": "ToolDispatchNotImplemented",
                    "message": (
                        "The model requested tool execution, but the assistant's "
                        "tool runner lands in slice 3 of AS-1. Re-run after that "
                        "slice merges, or rephrase to avoid tool use."
                    ),
                    "requested_tools": [t.name for t in result.tool_uses],
                },
            )
            yield AssistantStreamEvent(type=StreamEventType.DONE)
            return

        cost_usd = _estimate_cost_usd(choice.model, result.usage)
        full_text = "".join(text_buf) or result.text

        # Persist cache entry ----------------------------------------------------------
        cached = self._cache.store(
            key=cache_key,
            payload={
                "text": full_text,
                "stop_reason": result.stop_reason,
                "tool_uses": [],  # invariant in slice 2; tested below
                "usage": {
                    "tokens_in": result.usage.tokens_in,
                    "tokens_out": result.usage.tokens_out,
                    "cache_read_input_tokens": result.usage.cache_read_input_tokens,
                    "cache_creation_input_tokens": result.usage.cache_creation_input_tokens,
                },
            },
            tokens_in=result.usage.tokens_in,
            tokens_out=result.usage.tokens_out,
            cost_usd=cost_usd,
        )

        yield AssistantStreamEvent(
            type=StreamEventType.TURN_COMPLETED,
            payload={
                "turn_id": str(uuid.uuid4()),  # ephemeral in slice 2
                "model": choice.model,
                "tokens_in": result.usage.tokens_in,
                "tokens_out": result.usage.tokens_out,
                "cache_read_input_tokens": result.usage.cache_read_input_tokens,
                "cost_usd": cost_usd,
                "cache_hit": False,
                "stop_reason": result.stop_reason,
            },
        )
        yield AssistantStreamEvent(type=StreamEventType.DONE)

        # `cached` is what slice 4 will persist into the
        # `assistant_turns` table; for slice 2 it lives in the cache only.
        _ = cached

    async def confirm_tool_call(
        self,
        *,
        principal: Principal,
        conversation_id: str,
        request: ConfirmRequest,
    ) -> AsyncIterator[AssistantStreamEvent]:
        raise NotImplementedError("Write-tool confirmation lands in AS-2.")
        if False:  # pragma: no cover — needed only to make this an async-generator typed correctly
            yield AssistantStreamEvent(type=StreamEventType.DONE)

    async def cancel(
        self, *, principal: Principal, conversation_id: str
    ) -> None:
        # No-op in slice 2 — there's no persistent in-flight state to tear down
        # beyond what the streaming context manager already cleans up.
        await asyncio.sleep(0)

    async def load_conversation(
        self, *, principal: Principal, conversation_id: str
    ) -> tuple[Conversation, list[ConversationTurn]]:
        conv = self._conv_index.get(conversation_id)
        if conv is None or conv.owner_id != principal.tenant_id:
            raise KeyError(
                f"conversation {conversation_id!r} not found for tenant "
                f"{principal.tenant_id!r}"
            )
        # No turn persistence in slice 2 — returns empty list.
        return conv, []

    async def list_conversations(
        self,
        *,
        principal: Principal,
        limit: int = 50,
        include_deleted: bool = False,
    ) -> list[Conversation]:
        owned = [
            c for c in self._conv_index.values()
            if c.owner_id == principal.tenant_id
            and (include_deleted or c.deleted_at is None)
        ]
        owned.sort(key=lambda c: c.updated_at, reverse=True)
        return owned[:limit]

    # ─── Internal helpers ────────────────────────────────────────────

    def _build_system_blocks(self) -> list[dict[str, Any]]:
        """System prompt + Anthropic prompt-cache marker.

        The marker enables the 5-minute ephemeral cache on the system
        block. Identical (system, model, tool_defs) prefixes on
        subsequent turns hit the prompt cache for a heavy discount.

        Verified by `test_assistant_service_system_block_has_cache_control`.
        """
        return [
            {
                "type": "text",
                "text": self._prompt.text,
                "cache_control": {"type": "ephemeral"},
            }
        ]

    async def _replay_cached(
        self,
        hit: CachedResponse,
        *,
        choice: ModelChoice,
    ) -> AsyncIterator[AssistantStreamEvent]:
        """Replay a cached turn as a stream of events.

        Re-emits the text in one chunk (cached responses don't preserve
        original delta granularity), then a `TURN_COMPLETED` with
        `cache_hit=True` and `tokens_in/out=0` (replay cost is zero),
        then `DONE`.
        """
        text = str(hit.payload.get("text") or "")
        if text:
            yield AssistantStreamEvent(
                type=StreamEventType.TEXT_DELTA,
                payload={"text": text},
            )
        yield AssistantStreamEvent(
            type=StreamEventType.TURN_COMPLETED,
            payload={
                "turn_id": str(uuid.uuid4()),
                "model": choice.model,
                "tokens_in": 0,
                "tokens_out": 0,
                "cache_read_input_tokens": 0,
                "cost_usd": 0.0,
                "cache_hit": True,
                "stop_reason": hit.payload.get("stop_reason", "end_turn"),
            },
        )
        yield AssistantStreamEvent(type=StreamEventType.DONE)


__all__ = [
    "AnthropicLLMClient",
    "DefaultAssistantService",
    "LLMClient",
    "LLMResult",
    "LLMStream",
    "LLMToolUse",
    "LLMUsage",
]
