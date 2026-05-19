"""Tests for `stream.py` — SSE event encoder."""
from __future__ import annotations

import json

import pytest

from app.services.assistant.schemas import AssistantStreamEvent, StreamEventType
from app.services.assistant.stream import encode_sse, event_stream


def _event(event_type: StreamEventType, **payload) -> AssistantStreamEvent:
    return AssistantStreamEvent(type=event_type, payload=payload)


# ─────────────────────────────────────────────────────────────────────
# encode_sse
# ─────────────────────────────────────────────────────────────────────


def test_encode_sse_format() -> None:
    e = _event(StreamEventType.TEXT_DELTA, text="hello")
    line = encode_sse(e)
    assert line.startswith("data: ")
    assert line.endswith("\n\n")


def test_encode_sse_json_round_trip() -> None:
    e = _event(StreamEventType.TEXT_DELTA, text="world")
    line = encode_sse(e)
    body = json.loads(line.removeprefix("data: ").strip())
    assert body["type"] == "text_delta"
    assert body["payload"]["text"] == "world"


def test_encode_sse_turn_completed_payload() -> None:
    e = _event(
        StreamEventType.TURN_COMPLETED,
        model="claude-sonnet-4-6",
        tokens_in=100,
        tokens_out=42,
        cache_hit=False,
        stop_reason="end_turn",
        cost_usd=0.001,
    )
    line = encode_sse(e)
    body = json.loads(line.removeprefix("data: ").strip())
    assert body["type"] == "turn_completed"
    assert body["payload"]["model"] == "claude-sonnet-4-6"


def test_encode_sse_done_event() -> None:
    e = AssistantStreamEvent(type=StreamEventType.DONE)
    line = encode_sse(e)
    body = json.loads(line.removeprefix("data: ").strip())
    assert body["type"] == "done"
    assert body["payload"] == {}


def test_encode_sse_error_event() -> None:
    e = _event(StreamEventType.ERROR, kind="RuntimeError", message="boom")
    line = encode_sse(e)
    body = json.loads(line.removeprefix("data: ").strip())
    assert body["type"] == "error"
    assert body["payload"]["kind"] == "RuntimeError"


def test_encode_sse_tool_call_started() -> None:
    e = _event(StreamEventType.TOOL_CALL_STARTED, name="get_bars", call_id="tc_1", args={})
    line = encode_sse(e)
    body = json.loads(line.removeprefix("data: ").strip())
    assert body["type"] == "tool_call_started"
    assert body["payload"]["name"] == "get_bars"


def test_encode_sse_non_ascii_is_serialisable() -> None:
    e = _event(StreamEventType.TEXT_DELTA, text="résumé ™")
    line = encode_sse(e)
    body = json.loads(line.removeprefix("data: ").strip())
    assert body["payload"]["text"] == "résumé ™"


# ─────────────────────────────────────────────────────────────────────
# event_stream
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_event_stream_yields_encoded_events() -> None:
    async def _gen():
        yield _event(StreamEventType.TEXT_DELTA, text="a")
        yield _event(StreamEventType.TEXT_DELTA, text="b")
        yield AssistantStreamEvent(type=StreamEventType.DONE)

    chunks = [chunk async for chunk in event_stream(_gen())]
    assert len(chunks) == 3
    for chunk in chunks:
        assert chunk.startswith("data: ")
        assert chunk.endswith("\n\n")
        json.loads(chunk.removeprefix("data: ").strip())  # must be valid JSON


@pytest.mark.asyncio
async def test_event_stream_preserves_order() -> None:
    async def _gen():
        for i in range(5):
            yield _event(StreamEventType.TEXT_DELTA, text=str(i))

    chunks = [chunk async for chunk in event_stream(_gen())]
    texts = [json.loads(c.removeprefix("data: ").strip())["payload"]["text"] for c in chunks]
    assert texts == ["0", "1", "2", "3", "4"]
