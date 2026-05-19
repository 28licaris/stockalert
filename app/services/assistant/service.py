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
import hashlib
import json as _json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, AsyncIterator, Protocol, runtime_checkable

from app.services.assistant import prompts
from app.services.assistant.cache import CacheKeyInputs, CachedResponse, ResponseCache
from app.services.assistant.contract import Principal
from app.services.assistant.models import ModelChoice, ModelRegistry
from app.services.assistant.policy import ToolPolicy
from app.services.assistant.runner import ToolResult, ToolRunner
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

_MAX_TOOL_ITERATIONS: int = 10


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
    """Concrete `AssistantService` for AS-1 slices 2–3.

    Slice 2: text-only streaming, prompt-cache marker, SQLite response cache.
    Slice 3: tool dispatch via injected `ToolRunner` + `ToolPolicy`, multi-
             iteration turn loop, TOOL_CALL_STARTED / TOOL_RESULT events.

    Persistence + history is stubbed in-memory (slice 4 swaps in the
    ClickHouse-backed `ConversationStore`).
    """

    def __init__(
        self,
        *,
        client: LLMClient,
        cache: ResponseCache,
        models: ModelRegistry,
        prompt: prompts.SystemPrompt | None = None,
        policy: ToolPolicy | None = None,
        runner: ToolRunner | None = None,
        max_tool_iterations: int = _MAX_TOOL_ITERATIONS,
    ) -> None:
        self._client = client
        self._cache = cache
        self._models = models
        self._prompt = prompt or prompts.current()
        self._policy = policy
        self._runner = runner
        self._max_tool_iterations = max_tool_iterations
        # In-memory store stub — replaced in slice 4.
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
        """Stream one assistant turn, including any tool-call iterations.

        Event order for a tool-assisted turn:
          TEXT_DELTA* → TOOL_CALL_STARTED → TOOL_RESULT → TEXT_DELTA*
          → TURN_COMPLETED → DONE

        Cache: only text-only turns (stop_reason=end_turn with no tool
        calls) are stored. Tool-assisted turns are skipped — results are
        real-time data and must not be replayed from cache.
        """
        choice = self._models.pick(
            use_extended_thinking=request.use_extended_thinking,
            override_model=request.model,
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": request.user_msg}]

        # Build tool defs from the policy allowlist -------------------------------------
        tool_defs: list[dict[str, Any]] = []
        if self._policy and self._runner:
            allowed = self._policy.allowed_for(principal)
            tool_defs = await self._runner.get_tool_defs(allowed)
            # Mark the last tool schema with the ephemeral cache marker so
            # Anthropic can cache the (system + tool list) prefix across turns.
            if tool_defs:
                tool_defs[-1] = {**tool_defs[-1], "cache_control": {"type": "ephemeral"}}

        # Cache lookup -----------------------------------------------------------------
        tool_sha = (
            hashlib.sha256(_json.dumps(tool_defs, sort_keys=True).encode()).hexdigest()
            if tool_defs
            else ""
        )
        key_inputs = CacheKeyInputs(
            model=choice.model,
            system_prompt_sha256=self._prompt.sha256,
            tool_schema_sha256=tool_sha,
            messages=messages,
            tool_results=[],
            use_extended_thinking=request.use_extended_thinking,
        )
        cache_key = key_inputs.compute_key()
        hit = self._cache.lookup(cache_key)

        if hit is not None:
            logger.info(
                "assistant.turn cache hit key=%s… principal=%s",
                cache_key[:12],
                principal.user_id,
            )
            async for event in self._replay_cached(hit, choice=choice):
                yield event
            return

        # Multi-iteration turn loop ----------------------------------------------------
        system_blocks = self._build_system_blocks()
        used_tools = False
        final_result: Any = None
        final_text_buf: list[str] = []
        total_tokens_in = 0
        total_tokens_out = 0
        total_cache_read = 0

        for iteration in range(self._max_tool_iterations + 1):
            if iteration == self._max_tool_iterations:
                logger.warning(
                    "assistant.turn: max_tool_iterations=%d reached conv=%s",
                    self._max_tool_iterations,
                    conversation_id,
                )
                break

            try:
                async with self._client.stream(
                    model=choice.model,
                    system_blocks=system_blocks,
                    tools=tool_defs,
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
            except Exception as exc:  # noqa: BLE001 — surfaced as stream ERROR
                logger.exception(
                    "assistant.turn failed conv=%s principal=%s",
                    conversation_id,
                    principal.user_id,
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

            total_tokens_in += result.usage.tokens_in
            total_tokens_out += result.usage.tokens_out
            total_cache_read += result.usage.cache_read_input_tokens
            final_result = result
            final_text_buf = text_buf

            if result.tool_uses and self._runner:
                used_tools = True

                # Append the assistant turn (text + tool_use blocks) to the transcript
                assistant_content: list[dict[str, Any]] = []
                text_assembled = "".join(text_buf)
                if text_assembled:
                    assistant_content.append({"type": "text", "text": text_assembled})
                for tu in result.tool_uses:
                    assistant_content.append(
                        {
                            "type": "tool_use",
                            "id": tu.id,
                            "name": tu.name,
                            "input": tu.args,
                        }
                    )
                    yield AssistantStreamEvent(
                        type=StreamEventType.TOOL_CALL_STARTED,
                        payload={
                            "name": tu.name,
                            "call_id": tu.id,
                            "args": tu.args,
                        },
                    )
                messages.append({"role": "assistant", "content": assistant_content})

                # Dispatch tools and collect results
                tool_result_content: list[dict[str, Any]] = []
                for tu in result.tool_uses:
                    tr: ToolResult = await self._runner.run(tu.id, tu.name, tu.args)
                    yield AssistantStreamEvent(
                        type=StreamEventType.TOOL_RESULT,
                        payload={
                            "name": tr.name,
                            "call_id": tr.tool_call_id,
                            "content": tr.content,
                            "truncated": tr.truncated,
                            "error": tr.error,
                            "elapsed_s": tr.elapsed_s,
                        },
                    )
                    tool_result_content.append(
                        {
                            "type": "tool_result",
                            "tool_use_id": tr.tool_call_id,
                            "content": (
                                tr.content if not tr.error else f"Error: {tr.error}"
                            ),
                        }
                    )
                messages.append({"role": "user", "content": tool_result_content})
                # Continue to next iteration
            else:
                # No tool calls (or no runner) — turn is complete
                break

        if final_result is None:
            # Defensive guard — should not happen
            yield AssistantStreamEvent(type=StreamEventType.DONE)
            return

        cost_usd = _estimate_cost_usd(
            choice.model,
            LLMUsage(
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
                cache_read_input_tokens=total_cache_read,
            ),
        )
        full_text = "".join(final_text_buf) or final_result.text

        # Persist cache only for text-only turns (tool-assisted turns serve
        # real-time data that must not be replayed stale).
        if not used_tools:
            cached = self._cache.store(
                key=cache_key,
                payload={
                    "text": full_text,
                    "stop_reason": final_result.stop_reason,
                    "usage": {
                        "tokens_in": total_tokens_in,
                        "tokens_out": total_tokens_out,
                        "cache_read_input_tokens": total_cache_read,
                    },
                },
                tokens_in=total_tokens_in,
                tokens_out=total_tokens_out,
                cost_usd=cost_usd,
            )
            _ = cached  # slice 4 will persist this into the `assistant_turns` table

        yield AssistantStreamEvent(
            type=StreamEventType.TURN_COMPLETED,
            payload={
                "turn_id": str(uuid.uuid4()),  # ephemeral in slices 2–3
                "model": choice.model,
                "tokens_in": total_tokens_in,
                "tokens_out": total_tokens_out,
                "cache_read_input_tokens": total_cache_read,
                "cost_usd": cost_usd,
                "cache_hit": False,
                "stop_reason": final_result.stop_reason,
            },
        )
        yield AssistantStreamEvent(type=StreamEventType.DONE)

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
    "_MAX_TOOL_ITERATIONS",
]
