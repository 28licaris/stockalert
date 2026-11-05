"""Tests for `ModelRegistry`."""
from __future__ import annotations

from app.services.assistant.models import ModelChoice, ModelRegistry


def test_default_pick_is_sonnet() -> None:
    """Sonnet is the default model (README design decisions)."""
    choice = ModelRegistry().pick()
    assert choice.model == "claude-sonnet-5"
    assert choice.max_tokens > 0


def test_extended_thinking_picks_opus() -> None:
    """Opus serves the /explain opt-in."""
    choice = ModelRegistry().pick(use_extended_thinking=True)
    assert choice.model == "claude-opus-5"


def test_model_ids_carry_no_date_suffix() -> None:
    """Exact id strings only — a date-suffixed variant is not a valid id."""
    registry = ModelRegistry()
    for model in registry.known_models():
        tail = model.rsplit("-", 1)[-1]
        assert not tail.isdigit() or len(tail) < 8, f"date-suffixed id: {model}"


def test_extended_thinking_raises_effort_not_a_token_budget() -> None:
    """Depth is `output_config.effort`; the fixed thinking budget is gone.

    `thinking={"type": "enabled", "budget_tokens": N}` and any
    `temperature` return a 400 on these models, so `ModelChoice` must not
    carry either knob.
    """
    import dataclasses

    registry = ModelRegistry()
    fields = {f.name for f in dataclasses.fields(registry.pick())}
    assert "thinking_budget" not in fields
    assert "temperature" not in fields

    default = registry.pick()
    thinking = registry.pick(use_extended_thinking=True)
    tiers = ["low", "medium", "high", "xhigh", "max"]
    assert default.effort in tiers and thinking.effort in tiers
    assert tiers.index(thinking.effort) > tiers.index(default.effort)


def test_thinking_display_is_explicit() -> None:
    """The API default is "omitted" — empty blocks and a long visible pause."""
    assert ModelRegistry().pick().thinking_display == "summarized"


def test_override_model_wins_over_extended_thinking() -> None:
    """Caller-forced model id beats the use_extended_thinking flag."""
    choice = ModelRegistry().pick(
        use_extended_thinking=True,
        override_model="claude-haiku-4-5",
    )
    assert choice.model == "claude-haiku-4-5"


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
