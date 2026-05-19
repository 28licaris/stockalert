"""Unit tests for `routes_assistant.py`.

Uses a patched `_get_service()` so no real Anthropic, CH, or MCP calls.
The fake service drives the same `DefaultAssistantService` path that slice-2
unit tests use (no policy/runner/store), which keeps these tests fast and
self-contained.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.services.assistant.cache import ResponseCache
from app.services.assistant.models import ModelRegistry
from app.services.assistant.schemas import (
    DEFAULT_TENANT_ID,
    DEFAULT_USER_ID,
    ContinueRequest,
    StreamEventType,
)
from app.services.assistant.service import DefaultAssistantService


# ─────────────────────────────────────────────────────────────────────
# Fake LLM client (reuse _FakeLLMClient pattern from test_assistant_service)
# ─────────────────────────────────────────────────────────────────────


from dataclasses import dataclass
from typing import AsyncIterator


@dataclass
class _FakeLLMUsage:
    tokens_in: int = 10
    tokens_out: int = 5
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


from app.services.assistant.service import LLMResult, LLMUsage


class _FakeStream:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    def __aiter__(self) -> AsyncIterator[str]:
        return self._iter()

    async def _iter(self) -> AsyncIterator[str]:
        for c in self._chunks:
            yield c

    async def result(self) -> LLMResult:
        return LLMResult(
            text="".join(self._chunks),
            tool_uses=[],
            stop_reason="end_turn",
            usage=LLMUsage(tokens_in=10, tokens_out=5),
        )


class _FakeStreamCM:
    def __init__(self, chunks: list[str]) -> None:
        self._chunks = chunks

    async def __aenter__(self) -> _FakeStream:
        return _FakeStream(self._chunks)

    async def __aexit__(self, *args: Any) -> None:
        return None


class _FakeClient:
    def __init__(self, chunks: list[str] | None = None) -> None:
        self._chunks = chunks or ["hello world"]

    def stream(self, **_: Any) -> _FakeStreamCM:
        return _FakeStreamCM(self._chunks)


# ─────────────────────────────────────────────────────────────────────
# Service factory that bypasses real deps
# ─────────────────────────────────────────────────────────────────────


def _make_fake_service(tmp_path: Path, chunks: list[str] | None = None) -> DefaultAssistantService:
    return DefaultAssistantService(
        client=_FakeClient(chunks),
        cache=ResponseCache(tmp_path / "cache.sqlite"),
        models=ModelRegistry(),
    )


# ─────────────────────────────────────────────────────────────────────
# App client fixture
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture()
def api_client(tmp_path: Path):
    fake_svc = _make_fake_service(tmp_path)
    with patch("app.api.routes_assistant._get_service", return_value=fake_svc):
        from app.api.routes_assistant import router
        from fastapi import FastAPI

        app = FastAPI()
        app.include_router(router, prefix="/cockpit/assistant")
        with TestClient(app, raise_server_exceptions=True) as client:
            yield client


# ─────────────────────────────────────────────────────────────────────
# start_conversation
# ─────────────────────────────────────────────────────────────────────


def test_start_conversation_returns_200(api_client: TestClient) -> None:
    resp = api_client.post("/cockpit/assistant/conversations")
    assert resp.status_code == 200
    body = resp.json()
    assert "id" in body
    assert body["owner_id"] == DEFAULT_TENANT_ID
    assert body["user_id"] == DEFAULT_USER_ID


def test_start_conversation_with_title(api_client: TestClient) -> None:
    resp = api_client.post("/cockpit/assistant/conversations?title=My+Chat")
    assert resp.status_code == 200
    assert resp.json()["title"] == "My Chat"


# ─────────────────────────────────────────────────────────────────────
# list_conversations
# ─────────────────────────────────────────────────────────────────────


def test_list_conversations_returns_list(api_client: TestClient) -> None:
    # Create two conversations first
    api_client.post("/cockpit/assistant/conversations")
    api_client.post("/cockpit/assistant/conversations")
    resp = api_client.get("/cockpit/assistant/conversations")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)
    assert len(resp.json()) >= 2


# ─────────────────────────────────────────────────────────────────────
# load_conversation
# ─────────────────────────────────────────────────────────────────────


def test_load_conversation_returns_404_for_unknown(api_client: TestClient) -> None:
    resp = api_client.get("/cockpit/assistant/conversations/nonexistent-id")
    assert resp.status_code == 404


def test_load_conversation_returns_conv_and_turns(api_client: TestClient) -> None:
    create_resp = api_client.post("/cockpit/assistant/conversations")
    cid = create_resp.json()["id"]
    resp = api_client.get(f"/cockpit/assistant/conversations/{cid}")
    assert resp.status_code == 200
    body = resp.json()
    assert "conversation" in body
    assert "turns" in body
    assert body["conversation"]["id"] == cid


# ─────────────────────────────────────────────────────────────────────
# continue_conversation (SSE)
# ─────────────────────────────────────────────────────────────────────


def test_turn_streams_sse(tmp_path: Path) -> None:
    fake_svc = _make_fake_service(tmp_path, chunks=["Hello ", "world."])
    with patch("app.api.routes_assistant._get_service", return_value=fake_svc):
        from fastapi import FastAPI
        from app.api.routes_assistant import router

        app = FastAPI()
        app.include_router(router, prefix="/cockpit/assistant")
        with TestClient(app) as client:
            start = client.post("/cockpit/assistant/conversations")
            cid = start.json()["id"]
            resp = client.post(
                f"/cockpit/assistant/conversations/{cid}/turn",
                json={"user_msg": "hello"},
            )
    assert resp.status_code == 200
    assert "text/event-stream" in resp.headers["content-type"]

    lines = [l for l in resp.text.splitlines() if l.startswith("data: ")]
    events = [json.loads(l.removeprefix("data: ")) for l in lines]
    types = [e["type"] for e in events]
    assert "text_delta" in types
    assert "turn_completed" in types
    assert types[-1] == "done"


def test_turn_sse_text_delta_payload(tmp_path: Path) -> None:
    fake_svc = _make_fake_service(tmp_path, chunks=["chunk1", "chunk2"])
    with patch("app.api.routes_assistant._get_service", return_value=fake_svc):
        from fastapi import FastAPI
        from app.api.routes_assistant import router

        app = FastAPI()
        app.include_router(router, prefix="/cockpit/assistant")
        with TestClient(app) as client:
            cid = client.post("/cockpit/assistant/conversations").json()["id"]
            resp = client.post(
                f"/cockpit/assistant/conversations/{cid}/turn",
                json={"user_msg": "hi"},
            )

    lines = [l for l in resp.text.splitlines() if l.startswith("data: ")]
    events = [json.loads(l.removeprefix("data: ")) for l in lines]
    text_events = [e for e in events if e["type"] == "text_delta"]
    texts = [e["payload"]["text"] for e in text_events]
    assert texts == ["chunk1", "chunk2"]


# ─────────────────────────────────────────────────────────────────────
# confirm (501 stub)
# ─────────────────────────────────────────────────────────────────────


def test_confirm_returns_501(api_client: TestClient) -> None:
    resp = api_client.post(
        "/cockpit/assistant/conversations/any-id/confirm",
        json={"tool_call_id": "tc_1", "decision": "confirm"},
    )
    assert resp.status_code == 501


# ─────────────────────────────────────────────────────────────────────
# cancel
# ─────────────────────────────────────────────────────────────────────


def test_cancel_returns_200(api_client: TestClient) -> None:
    resp = api_client.delete("/cockpit/assistant/conversations/any-id")
    assert resp.status_code == 200
    assert resp.json()["cancelled"] is True
