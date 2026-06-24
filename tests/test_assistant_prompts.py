"""Tests for the system-prompt registry."""
from __future__ import annotations

import hashlib

import pytest

from app.services.assistant import prompts


def test_current_returns_v2() -> None:
    p = prompts.current()
    assert p.version == "v2"
    assert isinstance(p.text, str)
    assert len(p.text) > 200, "prompt should be substantial — sanity check"
    # EW-8: the active prompt must teach the wave tools + doctrine.
    assert "elliott wave" in p.text.lower()
    assert "get_wave_state" in p.text


def test_hash_matches_sha256_of_text() -> None:
    p = prompts.current()
    expected = hashlib.sha256(p.text.encode("utf-8")).hexdigest()
    assert p.sha256 == expected
    assert len(p.sha256) == 64


def test_short_hash_is_first_12_chars() -> None:
    p = prompts.current()
    assert p.short_hash == p.sha256[:12]


def test_load_unknown_version_raises() -> None:
    with pytest.raises(FileNotFoundError):
        prompts.load("does-not-exist")


def test_versions_includes_v1() -> None:
    assert "v1" in prompts.versions()


def test_current_is_cached() -> None:
    """Re-loading should be the identical instance (lru_cache on file I/O)."""
    a = prompts.current()
    b = prompts.current()
    assert a is b


def test_prompt_text_scopes_to_platform() -> None:
    """The prompt must establish platform scope + tool-grounding rules."""
    text = prompts.current().text.lower()
    # Specific anchors so a careless rewrite that drops the safety
    # rails surfaces in CI.
    assert "stockalert" in text
    assert "tool" in text
    # Refusal of fabrication is the cornerstone safety property.
    assert "never invent" in text or "do not invent" in text
    # The order-routing block must be explicit.
    assert "order" in text


def test_prompt_warns_about_prompt_injection_in_tool_results() -> None:
    """Plan §13.4 — the system prompt is the only place this is enforced."""
    text = prompts.current().text.lower()
    assert "tool_result" in text
    assert "ignore" in text and ("directive" in text or "instruction" in text)
