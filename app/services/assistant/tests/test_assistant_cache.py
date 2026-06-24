"""Tests for `ResponseCache` and `CacheKeyInputs`."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.assistant.cache import CacheKeyInputs, ResponseCache


# ─────────────────────────────────────────────────────────────────────
# Cache key — determinism + sensitivity
# ─────────────────────────────────────────────────────────────────────


def _inputs(**overrides: object) -> CacheKeyInputs:
    base = dict(
        model="claude-sonnet-4-6",
        system_prompt_sha256="a" * 64,
        tool_schema_sha256="b" * 64,
        messages=[{"role": "user", "content": "hi"}],
        tool_results=[],
        use_extended_thinking=False,
    )
    base.update(overrides)  # type: ignore[arg-type]
    return CacheKeyInputs(**base)  # type: ignore[arg-type]


def test_compute_key_is_deterministic() -> None:
    a = _inputs().compute_key()
    b = _inputs().compute_key()
    assert a == b
    assert len(a) == 64  # sha256 hex


def test_compute_key_changes_with_model() -> None:
    a = _inputs(model="claude-sonnet-4-6").compute_key()
    b = _inputs(model="claude-opus-4-7").compute_key()
    assert a != b


def test_compute_key_changes_with_system_prompt_hash() -> None:
    """Editing the system prompt MUST invalidate cached responses."""
    a = _inputs(system_prompt_sha256="a" * 64).compute_key()
    b = _inputs(system_prompt_sha256="c" * 64).compute_key()
    assert a != b


def test_compute_key_changes_with_tool_schema_hash() -> None:
    a = _inputs(tool_schema_sha256="b" * 64).compute_key()
    b = _inputs(tool_schema_sha256="d" * 64).compute_key()
    assert a != b


def test_compute_key_changes_with_messages() -> None:
    a = _inputs(messages=[{"role": "user", "content": "hi"}]).compute_key()
    b = _inputs(messages=[{"role": "user", "content": "hello"}]).compute_key()
    assert a != b


def test_compute_key_changes_with_tool_results() -> None:
    a = _inputs(tool_results=[]).compute_key()
    b = _inputs(tool_results=[{"name": "x", "ok": True}]).compute_key()
    assert a != b


def test_compute_key_changes_with_extended_thinking() -> None:
    a = _inputs(use_extended_thinking=False).compute_key()
    b = _inputs(use_extended_thinking=True).compute_key()
    assert a != b


def test_compute_key_independent_of_dict_ordering() -> None:
    """Wire-format stability: dict key order MUST not leak into the hash."""
    a = _inputs(messages=[{"role": "user", "content": "hi"}]).compute_key()
    b = _inputs(messages=[{"content": "hi", "role": "user"}]).compute_key()
    assert a == b


# ─────────────────────────────────────────────────────────────────────
# SQLite cache — store / lookup / clear
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def cache(tmp_path: Path) -> ResponseCache:
    return ResponseCache(tmp_path / "assistant_test.sqlite")


def test_lookup_returns_none_when_missing(cache: ResponseCache) -> None:
    assert cache.lookup("nonexistent-key") is None


def test_store_then_lookup_roundtrips(cache: ResponseCache) -> None:
    payload = {
        "text": "Bronze is fresh through 2026-05-18.",
        "stop_reason": "end_turn",
        "usage": {"tokens_in": 100, "tokens_out": 50},
    }
    cache.store(
        key="key-1",
        payload=payload,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.0008,
    )
    hit = cache.lookup("key-1")
    assert hit is not None
    assert hit.payload == payload
    assert hit.tokens_in == 100
    assert hit.tokens_out == 50
    assert hit.cost_usd == pytest.approx(0.0008)
    assert hit.created_at > 0.0


def test_store_overwrites_same_key(cache: ResponseCache) -> None:
    cache.store(key="k", payload={"v": 1}, tokens_in=10, tokens_out=10, cost_usd=0.01)
    cache.store(key="k", payload={"v": 2}, tokens_in=20, tokens_out=20, cost_usd=0.02)
    hit = cache.lookup("k")
    assert hit is not None
    assert hit.payload == {"v": 2}
    assert hit.tokens_in == 20


def test_clear_removes_all(cache: ResponseCache) -> None:
    for i in range(3):
        cache.store(key=f"k{i}", payload={"i": i}, tokens_in=1, tokens_out=1, cost_usd=0.0)
    assert len(list(cache.keys())) == 3
    n = cache.clear()
    assert n == 3
    assert len(list(cache.keys())) == 0


def test_cache_survives_close_and_reopen(tmp_path: Path) -> None:
    """SQLite is the persistence layer — restart-survival is the point."""
    db = tmp_path / "persist.sqlite"
    a = ResponseCache(db)
    a.store(key="kept", payload={"x": 1}, tokens_in=1, tokens_out=1, cost_usd=0.0)
    a.close()

    b = ResponseCache(db)
    hit = b.lookup("kept")
    assert hit is not None
    assert hit.payload == {"x": 1}
    b.close()


def test_cache_creates_parent_directory(tmp_path: Path) -> None:
    """`.cache/assistant_responses.sqlite` is the production default —
    the cache must create its parent dir on first run."""
    nested = tmp_path / "nested" / "cache.sqlite"
    assert not nested.parent.exists()
    ResponseCache(nested)
    assert nested.parent.exists()
    assert nested.exists()
