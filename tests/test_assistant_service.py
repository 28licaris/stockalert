"""Unit tests for `DefaultAssistantService`.

Slice 2 scope: text-only turn loop, prompt-cache marker on the system
block, cache hit/miss, Anthropic-failure stream-error path, slice-3
tool-dispatch stub. No real Anthropic calls — every test uses a fake
`LLMClient` that conforms to the Protocol declared in `service.py`.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator

import pytest

from app.services.assistant.cache import ResponseCache
from app.services.assistant.models import ModelRegistry
from app.services.assistant.prompts import SystemPrompt
from app.services.assistant.schemas import (
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    AssistantStreamEvent,
    ConfirmRequest,
    ContinueRequest,
    StreamEventType,
)
from app.services.assistant.service import (
    DefaultAssistantService,
    LLMResult,
    LLMToolUse,
    LLMUsage,
)


# ─────────────────────────────────────────────────────────────────────
# Test doubles
# ─────────────────────────────────────────────────────────────────────


@dataclass
class _StubPrincipal:
    user_id: str = DEFAULT_USER_ID
    tenant_id: str = DEFAULT_TENANT_ID
    roles: tuple[str, ...] = ("owner",)  # tuple for hashability; same shape
    plan: str = "dev"


class _FakeStream:
    """In-memory stand-in for an Anthropic AsyncMessageStream."""

    def __init__(
        self,
        *,
        chunks: list[str],
        tool_uses: list[LLMToolUse],
        usage: LLMUsage,
        raise_on_iter: BaseException | None = None,
    ) -> None:
        self._chunks = chunks
        self._tool_uses = tool_uses
        self._usage = usage
        self._raise = raise_on_iter

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[str]:
        if self._raise:
            raise self._raise
        for c in self._chunks:
            yield c

    async def result(self) -> LLMResult:
        return LLMResult(
            text="".join(self._chunks),
            tool_uses=self._tool_uses,
            stop_reason="tool_use" if self._tool_uses else "end_turn",
            usage=self._usage,
        )


class _FakeStreamCM:
    def __init__(self, stream: _FakeStream) -> None:
        self._stream = stream

    async def __aenter__(self) -> _FakeStream:
        return self._stream

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeLLMClient:
    """Captures stream() kwargs + yields a predetermined stream."""

    def __init__(
        self,
        *,
        chunks: list[str] | None = None,
        tool_uses: list[LLMToolUse] | None = None,
        usage: LLMUsage | None = None,
        raise_on_iter: BaseException | None = None,
    ) -> None:
        self.calls: list[dict[str, Any]] = []
        self._chunks = chunks if chunks is not None else ["Bronze ", "is ", "fresh."]
        self._tool_uses = tool_uses or []
        self._usage = usage or LLMUsage(tokens_in=120, tokens_out=42)
        self._raise = raise_on_iter

    def stream(self, **kwargs: Any) -> _FakeStreamCM:
        self.calls.append(kwargs)
        return _FakeStreamCM(
            _FakeStream(
                chunks=self._chunks,
                tool_uses=self._tool_uses,
                usage=self._usage,
                raise_on_iter=self._raise,
            )
        )


def _service(
    *,
    tmp_path: Path,
    client: _FakeLLMClient | None = None,
    models: ModelRegistry | None = None,
    prompt: SystemPrompt | None = None,
) -> DefaultAssistantService:
    return DefaultAssistantService(
        client=client or _FakeLLMClient(),
        cache=ResponseCache(tmp_path / "cache.sqlite"),
        models=models or ModelRegistry(),
        prompt=prompt,
    )


async def _drain(
    agen: AsyncIterator[AssistantStreamEvent],
) -> list[AssistantStreamEvent]:
    return [e async for e in agen]


# ─────────────────────────────────────────────────────────────────────
# Happy path — text-only turn
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_basic_text_turn_emits_deltas_then_completion(tmp_path: Path) -> None:
    client = _FakeLLMClient(chunks=["Hello ", "world."])
    svc = _service(tmp_path=tmp_path, client=client)

    events = await _drain(
        svc.continue_conversation(
            principal=_StubPrincipal(),
            conversation_id="conv-x",
            request=ContinueRequest(user_msg="hi"),
        )
    )

    types = [e.type for e in events]
    # Two text deltas, one completion, one terminal done.
    assert types == [
        StreamEventType.TEXT_DELTA,
        StreamEventType.TEXT_DELTA,
        StreamEventType.TURN_COMPLETED,
        StreamEventType.DONE,
    ]
    assert [e.payload["text"] for e in events if e.type == StreamEventType.TEXT_DELTA] == [
        "Hello ",
        "world.",
    ]
    completion = events[-2].payload
    assert completion["cache_hit"] is False
    assert completion["stop_reason"] == "end_turn"
    assert completion["tokens_in"] == 120
    assert completion["tokens_out"] == 42
    assert completion["model"] == "claude-sonnet-4-6"


# ─────────────────────────────────────────────────────────────────────
# Prompt-caching marker (plan §10, slice 2 acceptance criterion)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_system_block_has_cache_control_marker(tmp_path: Path) -> None:
    """The Anthropic ephemeral-prompt-cache discount requires this marker."""
    client = _FakeLLMClient()
    svc = _service(tmp_path=tmp_path, client=client)

    await _drain(
        svc.continue_conversation(
            principal=_StubPrincipal(),
            conversation_id="conv-x",
            request=ContinueRequest(user_msg="hi"),
        )
    )

    assert len(client.calls) == 1
    kwargs = client.calls[0]
    system = kwargs["system_blocks"]
    assert isinstance(system, list) and len(system) == 1
    assert system[0]["type"] == "text"
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    # Must carry the v1 prompt text, not some test placeholder.
    assert "StockAlert" in system[0]["text"]


@pytest.mark.asyncio
async def test_passes_model_kwargs_correctly(tmp_path: Path) -> None:
    client = _FakeLLMClient()
    svc = _service(tmp_path=tmp_path, client=client)

    await _drain(
        svc.continue_conversation(
            principal=_StubPrincipal(),
            conversation_id="c",
            request=ContinueRequest(user_msg="hi"),
        )
    )

    kwargs = client.calls[0]
    assert kwargs["model"] == "claude-sonnet-4-6"
    assert kwargs["temperature"] == 0.0
    assert kwargs["max_tokens"] > 0
    assert kwargs["thinking_budget"] is None
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert kwargs["tools"] == []  # slice 3 supplies these


@pytest.mark.asyncio
async def test_extended_thinking_invokes_opus_with_thinking_budget(
    tmp_path: Path,
) -> None:
    client = _FakeLLMClient()
    svc = _service(tmp_path=tmp_path, client=client)

    await _drain(
        svc.continue_conversation(
            principal=_StubPrincipal(),
            conversation_id="c",
            request=ContinueRequest(
                user_msg="explain why this divergence formed",
                use_extended_thinking=True,
            ),
        )
    )

    kwargs = client.calls[0]
    assert kwargs["model"] == "claude-opus-4-7"
    assert kwargs["thinking_budget"] is not None and kwargs["thinking_budget"] > 0
    assert kwargs["temperature"] == 1.0


# ─────────────────────────────────────────────────────────────────────
# Error paths — no silent failures (CLAUDE.md prime directive)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_anthropic_exception_emits_error_then_done(tmp_path: Path) -> None:
    """Plan §13.x + CLAUDE prime directive: every failure surfaces in the stream."""
    boom = RuntimeError("anthropic 500")
    client = _FakeLLMClient(raise_on_iter=boom)
    svc = _service(tmp_path=tmp_path, client=client)

    events = await _drain(
        svc.continue_conversation(
            principal=_StubPrincipal(),
            conversation_id="c",
            request=ContinueRequest(user_msg="hi"),
        )
    )

    types = [e.type for e in events]
    assert StreamEventType.ERROR in types
    assert types[-1] == StreamEventType.DONE
    err = next(e for e in events if e.type == StreamEventType.ERROR)
    assert err.payload["kind"] == "RuntimeError"
    assert "anthropic 500" in err.payload["message"]


@pytest.mark.asyncio
async def test_tool_use_returns_not_implemented_error_in_slice_2(
    tmp_path: Path,
) -> None:
    """If the LLM emits a tool_use, slice 2 surfaces a structured error
    (rather than silently dropping the request). Wired in slice 3."""
    client = _FakeLLMClient(
        chunks=["Looking up freshness... "],
        tool_uses=[LLMToolUse(id="tc_1", name="get_lake_freshness", args={})],
    )
    svc = _service(tmp_path=tmp_path, client=client)

    events = await _drain(
        svc.continue_conversation(
            principal=_StubPrincipal(),
            conversation_id="c",
            request=ContinueRequest(user_msg="freshness?"),
        )
    )

    err = next(e for e in events if e.type == StreamEventType.ERROR)
    assert err.payload["kind"] == "ToolDispatchNotImplemented"
    assert "slice 3" in err.payload["message"]
    assert err.payload["requested_tools"] == ["get_lake_freshness"]
    assert events[-1].type == StreamEventType.DONE


# ─────────────────────────────────────────────────────────────────────
# Cache integration
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cache_miss_then_hit(tmp_path: Path) -> None:
    """Second identical request must hit the cache and skip the LLM."""
    client = _FakeLLMClient(chunks=["cached!"])
    svc = _service(tmp_path=tmp_path, client=client)

    # First call → LLM
    events1 = await _drain(
        svc.continue_conversation(
            principal=_StubPrincipal(),
            conversation_id="c1",
            request=ContinueRequest(user_msg="same"),
        )
    )
    # Second call → cache
    events2 = await _drain(
        svc.continue_conversation(
            principal=_StubPrincipal(),
            conversation_id="c2",
            request=ContinueRequest(user_msg="same"),
        )
    )

    assert len(client.calls) == 1, "second call must be served from cache"

    completion1 = next(e for e in events1 if e.type == StreamEventType.TURN_COMPLETED)
    completion2 = next(e for e in events2 if e.type == StreamEventType.TURN_COMPLETED)
    assert completion1.payload["cache_hit"] is False
    assert completion2.payload["cache_hit"] is True
    assert completion2.payload["tokens_in"] == 0
    assert completion2.payload["tokens_out"] == 0
    assert completion2.payload["cost_usd"] == 0.0


@pytest.mark.asyncio
async def test_cache_key_changes_when_user_msg_changes(tmp_path: Path) -> None:
    client = _FakeLLMClient(chunks=["resp"])
    svc = _service(tmp_path=tmp_path, client=client)

    await _drain(
        svc.continue_conversation(
            principal=_StubPrincipal(),
            conversation_id="c",
            request=ContinueRequest(user_msg="A"),
        )
    )
    await _drain(
        svc.continue_conversation(
            principal=_StubPrincipal(),
            conversation_id="c",
            request=ContinueRequest(user_msg="B"),
        )
    )

    # Different user_msg → different cache key → two LLM calls.
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_cache_key_changes_when_extended_thinking_changes(
    tmp_path: Path,
) -> None:
    client = _FakeLLMClient(chunks=["resp"])
    svc = _service(tmp_path=tmp_path, client=client)

    base = dict(principal=_StubPrincipal(), conversation_id="c")
    await _drain(
        svc.continue_conversation(
            **base, request=ContinueRequest(user_msg="x", use_extended_thinking=False)
        )
    )
    await _drain(
        svc.continue_conversation(
            **base, request=ContinueRequest(user_msg="x", use_extended_thinking=True)
        )
    )

    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_cache_key_changes_when_system_prompt_changes(tmp_path: Path) -> None:
    """Plan §10.2 + slice 2 acceptance: editing prompts/v1.md invalidates caches."""
    client = _FakeLLMClient(chunks=["resp"])
    prompt_a = SystemPrompt(version="testA", text="prompt A", sha256="a" * 64)
    prompt_b = SystemPrompt(version="testB", text="prompt B", sha256="b" * 64)

    svc_a = _service(tmp_path=tmp_path, client=client, prompt=prompt_a)
    svc_b = DefaultAssistantService(
        client=client,
        cache=svc_a._cache,  # share the cache between services
        models=ModelRegistry(),
        prompt=prompt_b,
    )

    await _drain(
        svc_a.continue_conversation(
            principal=_StubPrincipal(),
            conversation_id="c",
            request=ContinueRequest(user_msg="x"),
        )
    )
    await _drain(
        svc_b.continue_conversation(
            principal=_StubPrincipal(),
            conversation_id="c",
            request=ContinueRequest(user_msg="x"),
        )
    )

    assert len(client.calls) == 2, "different system prompts must not share cache entries"


# ─────────────────────────────────────────────────────────────────────
# Conversation lifecycle stubs (in-memory in slice 2)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_start_conversation_assigns_owner_id(tmp_path: Path) -> None:
    svc = _service(tmp_path=tmp_path)
    conv = await svc.start_conversation(principal=_StubPrincipal())
    assert conv.owner_id == DEFAULT_TENANT_ID
    assert conv.user_id == DEFAULT_USER_ID
    assert conv.turn_count == 0


@pytest.mark.asyncio
async def test_load_conversation_rejects_other_tenants(tmp_path: Path) -> None:
    """SaaS-readiness: a tenant must not read another tenant's conversation."""
    svc = _service(tmp_path=tmp_path)
    owner = _StubPrincipal(tenant_id="tenant-A")
    other = _StubPrincipal(tenant_id="tenant-B")
    conv = await svc.start_conversation(principal=owner)
    with pytest.raises(KeyError):
        await svc.load_conversation(principal=other, conversation_id=conv.id)


@pytest.mark.asyncio
async def test_list_conversations_returns_only_owned(tmp_path: Path) -> None:
    svc = _service(tmp_path=tmp_path)
    owner = _StubPrincipal(tenant_id="tenant-A")
    other = _StubPrincipal(tenant_id="tenant-B")
    own_conv = await svc.start_conversation(principal=owner)
    _ = await svc.start_conversation(principal=other)
    owned = await svc.list_conversations(principal=owner)
    assert [c.id for c in owned] == [own_conv.id]


@pytest.mark.asyncio
async def test_confirm_tool_call_raises_not_implemented(tmp_path: Path) -> None:
    """Confirm is wired in AS-2 (write tools). Slice 2 surfaces this clearly."""
    svc = _service(tmp_path=tmp_path)
    agen = svc.confirm_tool_call(
        principal=_StubPrincipal(),
        conversation_id="c",
        request=ConfirmRequest(tool_call_id="tc_1", decision="confirm"),
    )
    with pytest.raises(NotImplementedError):
        async for _ in agen:
            pass


@pytest.mark.asyncio
async def test_cancel_is_noop(tmp_path: Path) -> None:
    """Slice 2 has no in-flight state to tear down; cancel must not raise."""
    svc = _service(tmp_path=tmp_path)
    await svc.cancel(principal=_StubPrincipal(), conversation_id="c")
