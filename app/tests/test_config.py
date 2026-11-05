"""Unit tests for ``app/config.py`` provider-role resolution.

Pins the ``STREAM_PROVIDER=none`` sentinel contract that the AWS deployment's
``api`` container and ``.claude/launch.json`` rely on (deployment_dev.md §3):

* ``none`` (any case, surrounding whitespace) → ``effective_stream_provider == ""``
* an *empty* ``STREAM_PROVIDER`` still falls back to ``DATA_PROVIDER``
* ``effective_history_provider`` never sees the sentinel
* ``get_stream_provider()`` refuses to build a provider when disabled instead
  of silently falling back to ``DATA_PROVIDER``
"""
from __future__ import annotations

import pytest

from app import config
from app.config import STREAM_PROVIDER_NONE, Settings


@pytest.mark.parametrize("raw", ["none", "NONE", "None", "  none  "])
def test_stream_provider_none_sentinel_disables_streaming(raw: str) -> None:
    s = Settings(stream_provider=raw, data_provider="schwab")
    assert s.effective_stream_provider == ""


def test_empty_stream_provider_still_falls_back_to_data_provider() -> None:
    # The documented default: empty = inherit. This is exactly why the sentinel
    # exists — pin it so nobody "fixes" it the other way and breaks .env.example.
    s = Settings(stream_provider="", data_provider="schwab")
    assert s.effective_stream_provider == "schwab"


def test_explicit_stream_provider_is_normalised() -> None:
    s = Settings(stream_provider=" Polygon ", data_provider="schwab")
    assert s.effective_stream_provider == "polygon"


def test_sentinel_does_not_touch_history_provider() -> None:
    s = Settings(stream_provider="none", history_provider="", data_provider="schwab")
    assert s.effective_history_provider == "schwab"
    s2 = Settings(stream_provider="none", history_provider="polygon", data_provider="schwab")
    assert s2.effective_history_provider == "polygon"


def test_sentinel_constant_is_the_documented_value() -> None:
    assert STREAM_PROVIDER_NONE == "none"


def test_get_stream_provider_raises_when_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config, "settings", Settings(stream_provider="none", data_provider="schwab")
    )
    with pytest.raises(ValueError, match="STREAM_PROVIDER=none"):
        config.get_stream_provider()


def test_get_stream_provider_uses_effective_name(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[str | None] = []
    monkeypatch.setattr(
        config, "settings", Settings(stream_provider="polygon", data_provider="schwab")
    )
    monkeypatch.setattr(config, "get_provider", lambda name=None: seen.append(name) or "P")
    assert config.get_stream_provider() == "P"
    assert seen == ["polygon"]
