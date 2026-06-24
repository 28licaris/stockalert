"""Unit tests for `DefaultAssistantService`.

Slice 2: text-only turn loop, prompt-cache marker, cache hit/miss,
Anthropic-failure stream-error path.

Slice 3: tool dispatch round-trip (fake runner + fake policy), denied tools
excluded from the prompt, LAST tool block carries the ephemeral cache marker,
multi-iteration turn terminates at `max_tool_iterations`. No real Anthropic
calls — every test uses a fake `LLMClient`.
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
from app.services.assistant.policy import DevModeToolPolicy, ToolPolicy
from app.services.assistant.runner import ToolResult, ToolRunner
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
    policy: ToolPolicy | None = None,
    runner: ToolRunner | None = None,
    max_tool_iterations: int = 10,
) -> DefaultAssistantService:
    return DefaultAssistantService(
        client=client or _FakeLLMClient(),
        cache=ResponseCache(tmp_path / "cache.sqlite"),
        models=models or ModelRegistry(),
        prompt=prompt,
        policy=policy,
        runner=runner,
        max_tool_iterations=max_tool_iterations,
    )


# ─────────────────────────────────────────────────────────────────────
# Fake runner + policy for slice 3 tests
# ─────────────────────────────────────────────────────────────────────


class _FakeToolRunner:
    """Captures run() calls and returns canned ToolResults."""

    def __init__(
        self,
        tool_defs: list[dict[str, Any]] | None = None,
        results: dict[str, ToolResult] | None = None,
    ) -> None:
        self._tool_defs = tool_defs or []
        self._results = results or {}
        self.run_calls: list[tuple[str, str, dict[str, Any]]] = []

    async def get_tool_defs(self, allowed_names: list[str]) -> list[dict[str, Any]]:
        return [d for d in self._tool_defs if d["name"] in allowed_names]

    async def run(
        self, tool_call_id: str, name: str, args: dict[str, Any]
    ) -> ToolResult:
        self.run_calls.append((tool_call_id, name, args))
        if name in self._results:
            return self._results[name]
        return ToolResult(tool_call_id=tool_call_id, name=name, content='{"ok": true}')


def _fake_tool_def(name: str) -> dict[str, Any]:
    return {
        "name": name,
        "description": f"Fake {name} tool",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    }


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


# ─────────────────────────────────────────────────────────────────────
# Slice 3 — tool dispatch
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tool_dispatch_round_trip(tmp_path: Path) -> None:
    """A turn that triggers one tool call yields the expected event sequence:
    TEXT_DELTA* → TOOL_CALL_STARTED → TOOL_RESULT → TEXT_DELTA* →
    TURN_COMPLETED → DONE.
    """
    # First LLM call: text + one tool_use
    # Second LLM call: text only (end_turn)
    tool_use = LLMToolUse(id="tc_1", name="get_bars", args={"symbol": "AAPL"})
    client = _FakeLLMClient(
        # side_effects list: first call has tool_uses, second has none
    )
    # Override client to return two different streams
    call_count = 0
    orig_stream = client.stream

    def _two_phase(**kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _FakeStreamCM(
                _FakeStream(
                    chunks=["Checking bars... "],
                    tool_uses=[tool_use],
                    usage=LLMUsage(tokens_in=100, tokens_out=20),
                )
            )
        return _FakeStreamCM(
            _FakeStream(
                chunks=["Here are the results."],
                tool_uses=[],
                usage=LLMUsage(tokens_in=50, tokens_out=10),
            )
        )

    client.stream = _two_phase  # type: ignore[method-assign]

    tool_result = ToolResult(
        tool_call_id="tc_1", name="get_bars", content='{"bars": []}', truncated=False
    )
    policy = DevModeToolPolicy(all_tool_names=["get_bars", "run_backtest"])
    fake_runner = _FakeToolRunner(
        tool_defs=[_fake_tool_def("get_bars")],
        results={"get_bars": tool_result},
    )
    svc = _service(tmp_path=tmp_path, client=client, policy=policy, runner=fake_runner)

    events = await _drain(
        svc.continue_conversation(
            principal=_StubPrincipal(),
            conversation_id="c",
            request=ContinueRequest(user_msg="give me AAPL bars"),
        )
    )

    types = [e.type for e in events]
    assert StreamEventType.TOOL_CALL_STARTED in types
    assert StreamEventType.TOOL_RESULT in types
    assert StreamEventType.TURN_COMPLETED in types
    assert types[-1] == StreamEventType.DONE

    # Order: all text deltas before TOOL_CALL_STARTED, more text after TOOL_RESULT
    started_idx = types.index(StreamEventType.TOOL_CALL_STARTED)
    result_idx = types.index(StreamEventType.TOOL_RESULT)
    assert started_idx < result_idx

    # Tool was dispatched
    assert fake_runner.run_calls == [("tc_1", "get_bars", {"symbol": "AAPL"})]


@pytest.mark.asyncio
async def test_denied_tools_excluded_from_prompt(tmp_path: Path) -> None:
    """Denied tools must never appear in the `tools=` parameter sent to the LLM."""
    client = _FakeLLMClient()
    policy = DevModeToolPolicy(all_tool_names=["get_bars", "run_backtest"])
    fake_runner = _FakeToolRunner(
        tool_defs=[_fake_tool_def("get_bars"), _fake_tool_def("run_backtest")]
    )
    svc = _service(tmp_path=tmp_path, client=client, policy=policy, runner=fake_runner)

    await _drain(
        svc.continue_conversation(
            principal=_StubPrincipal(),
            conversation_id="c",
            request=ContinueRequest(user_msg="hi"),
        )
    )

    kwargs = client.calls[0]
    tool_names_in_prompt = [t["name"] for t in kwargs["tools"]]
    assert "run_backtest" not in tool_names_in_prompt
    assert "get_bars" in tool_names_in_prompt


@pytest.mark.asyncio
async def test_last_tool_block_carries_ephemeral_cache_marker(tmp_path: Path) -> None:
    """The LAST tool schema block must have cache_control: ephemeral so
    Anthropic can cache the (system + tool_list) prefix."""
    client = _FakeLLMClient()
    policy = DevModeToolPolicy(all_tool_names=["tool_a", "tool_b", "tool_c"])
    fake_runner = _FakeToolRunner(
        tool_defs=[
            _fake_tool_def("tool_a"),
            _fake_tool_def("tool_b"),
            _fake_tool_def("tool_c"),
        ]
    )
    svc = _service(tmp_path=tmp_path, client=client, policy=policy, runner=fake_runner)

    await _drain(
        svc.continue_conversation(
            principal=_StubPrincipal(),
            conversation_id="c",
            request=ContinueRequest(user_msg="hi"),
        )
    )

    kwargs = client.calls[0]
    tools = kwargs["tools"]
    assert len(tools) == 3
    # Only the last tool carries the cache marker
    assert tools[-1].get("cache_control") == {"type": "ephemeral"}
    assert "cache_control" not in tools[0]
    assert "cache_control" not in tools[1]


@pytest.mark.asyncio
async def test_no_tools_when_no_policy_or_runner(tmp_path: Path) -> None:
    """Without policy/runner, the LLM sees an empty tools list."""
    client = _FakeLLMClient()
    svc = _service(tmp_path=tmp_path, client=client)  # no policy, no runner

    await _drain(
        svc.continue_conversation(
            principal=_StubPrincipal(),
            conversation_id="c",
            request=ContinueRequest(user_msg="hi"),
        )
    )

    assert client.calls[0]["tools"] == []


@pytest.mark.asyncio
async def test_multi_iteration_terminates_at_max_iterations(tmp_path: Path) -> None:
    """The turn loop must stop after max_tool_iterations even if the LLM
    keeps requesting tool calls."""
    # LLM always returns a tool_use (infinite loop without the cap)
    always_tool = LLMToolUse(id="tc_x", name="get_bars", args={})

    call_count = 0
    client = _FakeLLMClient()

    def _always_tools(**kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        return _FakeStreamCM(
            _FakeStream(
                chunks=[],
                tool_uses=[always_tool],
                usage=LLMUsage(tokens_in=10, tokens_out=5),
            )
        )

    client.stream = _always_tools  # type: ignore[method-assign]

    policy = DevModeToolPolicy(all_tool_names=["get_bars"])
    fake_runner = _FakeToolRunner(tool_defs=[_fake_tool_def("get_bars")])
    svc = _service(
        tmp_path=tmp_path,
        client=client,
        policy=policy,
        runner=fake_runner,
        max_tool_iterations=3,
    )

    events = await _drain(
        svc.continue_conversation(
            principal=_StubPrincipal(),
            conversation_id="c",
            request=ContinueRequest(user_msg="loop me"),
        )
    )

    # Must terminate — check DONE is emitted
    assert events[-1].type == StreamEventType.DONE
    # LLM was called at most max_tool_iterations times
    assert call_count <= 3


@pytest.mark.asyncio
async def test_tool_turn_not_cached(tmp_path: Path) -> None:
    """Turns that used tools must not be stored in the response cache.
    Tool results are real-time — replaying them stale would be wrong.
    """
    tool_use = LLMToolUse(id="tc_1", name="get_bars", args={})
    call_count = 0
    client = _FakeLLMClient()

    def _phase_stream(**kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if call_count % 2 == 1:
            return _FakeStreamCM(
                _FakeStream(
                    chunks=["Looking..."],
                    tool_uses=[tool_use],
                    usage=LLMUsage(tokens_in=10, tokens_out=5),
                )
            )
        return _FakeStreamCM(
            _FakeStream(
                chunks=["Done."],
                tool_uses=[],
                usage=LLMUsage(tokens_in=10, tokens_out=5),
            )
        )

    client.stream = _phase_stream  # type: ignore[method-assign]

    policy = DevModeToolPolicy(all_tool_names=["get_bars"])
    fake_runner = _FakeToolRunner(tool_defs=[_fake_tool_def("get_bars")])
    svc = _service(tmp_path=tmp_path, client=client, policy=policy, runner=fake_runner)

    request = ContinueRequest(user_msg="tool query")
    principal = _StubPrincipal()

    # First call
    await _drain(
        svc.continue_conversation(
            principal=principal, conversation_id="c1", request=request
        )
    )
    # Second identical call — must NOT hit cache (tool result could differ)
    await _drain(
        svc.continue_conversation(
            principal=principal, conversation_id="c2", request=request
        )
    )

    # If cached, client would only have been called twice total (1+0) or
    # three times (1 tool run + 1 text) on the first request.  Without caching
    # the second request makes another full round of calls.
    assert call_count >= 4, "second request should hit the LLM, not a cached replay"
