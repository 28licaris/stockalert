"""SSE event encoder for the assistant copilot.

Wire format (one event):
    data: {"type": "<event_type>", "payload": {...}}\n\n

The `\n\n` double-newline is the SSE record separator; browsers and
`EventSource` use it to delimit events.
"""
from __future__ import annotations

import json
from typing import AsyncIterator

from app.services.assistant.schemas import AssistantStreamEvent


def encode_sse(event: AssistantStreamEvent) -> str:
    """Encode one `AssistantStreamEvent` as a ready-to-write SSE data line."""
    body = json.dumps(
        {"type": str(event.type), "payload": event.payload},
        default=str,
    )
    return f"data: {body}\n\n"


async def event_stream(
    events: AsyncIterator[AssistantStreamEvent],
) -> AsyncIterator[str]:
    """Yield SSE-encoded strings from an assistant event generator."""
    async for event in events:
        yield encode_sse(event)


__all__ = ["encode_sse", "event_stream"]
