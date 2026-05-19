"""Model selection for the Assistant service.

One source of truth for which Anthropic model handles which kind of
turn. Today: Sonnet 4.6 by default; Opus 4.7 when the user opts in
to extended thinking. The registry shape supports more providers but
slice 2 only wires Anthropic.

Per the locked decision in `docs/assistant_plan.md §18`:
  - default = `claude-sonnet-4-6`
  - extended-thinking = `claude-opus-4-7`

The model id is part of the response-cache key (see `cache.py`).
Switching models therefore invalidates the cache — same intent as
switching the system prompt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final


# Anthropic model ids per the harness's knowledge cutoff (2026-01).
# Bump these in lockstep when a new family ships.
_SONNET: Final[str] = "claude-sonnet-4-6"
_OPUS: Final[str] = "claude-opus-4-7"


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """One picked model + its per-turn knobs."""

    model: str
    """Anthropic model id."""

    max_tokens: int
    """Hard cap on output tokens for this turn."""

    temperature: float
    """0.0 for tool-calling; nonzero for free-form explain turns."""

    thinking_budget: int | None = None
    """If set, request extended thinking with this token budget."""


class ModelRegistry:
    """Picks the right model + per-turn knobs.

    Construction takes overrides (mostly for tests). In production
    everything defaults to the locked values.
    """

    def __init__(
        self,
        *,
        default_model: str = _SONNET,
        thinking_model: str = _OPUS,
        default_max_tokens: int = 8000,
        thinking_max_tokens: int = 16000,
        thinking_budget: int = 4000,
        default_temperature: float = 0.0,
        thinking_temperature: float = 1.0,
    ) -> None:
        self._default_model = default_model
        self._thinking_model = thinking_model
        self._default_max_tokens = default_max_tokens
        self._thinking_max_tokens = thinking_max_tokens
        self._thinking_budget = thinking_budget
        self._default_temperature = default_temperature
        self._thinking_temperature = thinking_temperature

    def pick(
        self,
        *,
        use_extended_thinking: bool = False,
        override_model: str | None = None,
    ) -> ModelChoice:
        """Resolve a `ContinueRequest` to a concrete model + knobs.

        Precedence:
          1. `override_model` (caller forced a specific id)
          2. `use_extended_thinking=True` -> Opus + thinking budget
          3. default Sonnet
        """
        if override_model:
            return ModelChoice(
                model=override_model,
                max_tokens=self._default_max_tokens,
                temperature=self._default_temperature,
                thinking_budget=None,
            )
        if use_extended_thinking:
            # Extended thinking REQUIRES temperature=1 per the Anthropic
            # API contract; the call would 400 otherwise.
            return ModelChoice(
                model=self._thinking_model,
                max_tokens=self._thinking_max_tokens,
                temperature=self._thinking_temperature,
                thinking_budget=self._thinking_budget,
            )
        return ModelChoice(
            model=self._default_model,
            max_tokens=self._default_max_tokens,
            temperature=self._default_temperature,
            thinking_budget=None,
        )

    @property
    def default_model(self) -> str:
        return self._default_model

    @property
    def thinking_model(self) -> str:
        return self._thinking_model

    def known_models(self) -> list[str]:
        """All models this registry can serve. Used by the `GET /cockpit/assistant/models` endpoint (slice 5)."""
        return sorted({self._default_model, self._thinking_model})


__all__ = ["ModelChoice", "ModelRegistry"]
