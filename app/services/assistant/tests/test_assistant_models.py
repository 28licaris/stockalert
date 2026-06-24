"""Tests for `ModelRegistry`."""
from __future__ import annotations

from app.services.assistant.models import ModelChoice, ModelRegistry


def test_default_pick_is_sonnet_46() -> None:
    """Plan §18 decision 3: Sonnet 4.6 is the default model."""
    choice = ModelRegistry().pick()
    assert choice.model == "claude-sonnet-4-6"
    assert choice.temperature == 0.0
    assert choice.thinking_budget is None
    assert choice.max_tokens > 0


def test_extended_thinking_picks_opus_47() -> None:
    """Plan §18 decision 3: Opus 4.7 is the extended-thinking model."""
    choice = ModelRegistry().pick(use_extended_thinking=True)
    assert choice.model == "claude-opus-4-7"
    assert choice.thinking_budget is not None and choice.thinking_budget > 0


def test_extended_thinking_uses_temperature_one() -> None:
    """Anthropic API requires temperature=1 when thinking is enabled."""
    choice = ModelRegistry().pick(use_extended_thinking=True)
    assert choice.temperature == 1.0


def test_override_model_wins_over_extended_thinking() -> None:
    """Caller-forced model id beats the use_extended_thinking flag."""
    choice = ModelRegistry().pick(
        use_extended_thinking=True,
        override_model="claude-haiku-4-5-20251001",
    )
    assert choice.model == "claude-haiku-4-5-20251001"
    assert choice.thinking_budget is None


def test_known_models_includes_default_and_thinking() -> None:
    registry = ModelRegistry()
    known = registry.known_models()
    assert registry.default_model in known
    assert registry.thinking_model in known


def test_registry_accepts_overrides_for_tests() -> None:
    """Constructor overrides are how tests pin a specific model id."""
    registry = ModelRegistry(
        default_model="test-default",
        thinking_model="test-thinking",
        default_max_tokens=42,
    )
    default = registry.pick()
    assert default.model == "test-default"
    assert default.max_tokens == 42
    thinking = registry.pick(use_extended_thinking=True)
    assert thinking.model == "test-thinking"


def test_model_choice_is_frozen() -> None:
    """ModelChoice is a config snapshot — mutating downstream is a bug."""
    import dataclasses

    choice = ModelRegistry().pick()
    assert dataclasses.is_dataclass(choice)
    # frozen=True on the dataclass forbids attribute assignment.
    import pytest

    with pytest.raises(dataclasses.FrozenInstanceError):
        choice.model = "other"  # type: ignore[misc]
