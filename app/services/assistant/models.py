"""Model selection for the Assistant service.

One source of truth for which Anthropic model handles which kind of
turn. Sonnet by default; Opus when the user opts in to extended
thinking (`/explain`).

Per the locked decision recorded in `app/services/assistant/README.md`:
  - default           = `claude-sonnet-5`
  - extended-thinking = `claude-opus-5`

**Thinking is adaptive, not budgeted.** The fixed thinking-token-budget
API (`thinking={"type": "enabled", "budget_tokens": N}`) is REMOVED on
the current models and returns a 400 — as does any `temperature` /
`top_p` / `top_k`. Depth is controlled by `output_config.effort`
instead, and Claude decides per turn whether to think at all. See
`service.py::_AnthropicStreamCM` for the request shape.

Adaptive thinking stays ON for both paths deliberately. With thinking
disabled, the model can write a tool call into its *visible text*
rather than emitting a `tool_use` block — the turn succeeds, the call
never runs, and nothing raises. For a tool-dispatching assistant that
is the worst available failure mode, so we pay for thinking and tune
cost with `effort`.

The model id is part of the response-cache key (see `cache.py`), as is
`use_extended_thinking` — and effort is derived from that flag, so the
key already covers every knob here. Switching models invalidates the
cache, same intent as switching the system prompt.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Final


# Anthropic model ids. Exact strings — never append a date suffix.
# Bump these in lockstep when a new family ships, and re-check the
# thinking/effort contract at the same time: it has changed before.
_SONNET: Final[str] = "claude-sonnet-5"
_OPUS: Final[str] = "claude-opus-5"

# output_config.effort — low | medium | high | xhigh | max.
# `medium` for routine conversational turns; `xhigh` for an explicit
# /explain opt-in (the documented sweet spot for agentic work).
_DEFAULT_EFFORT: Final[str] = "medium"
_THINKING_EFFORT: Final[str] = "xhigh"


@dataclass(frozen=True, slots=True)
class ModelChoice:
    """One picked model + its per-turn knobs."""

    model: str
    """Anthropic model id."""

    max_tokens: int
    """Hard cap on output tokens for this turn."""

    effort: str
    """`output_config.effort` — controls thinking depth and token spend."""

    thinking_display: str = "summarized"
    """`thinking.display` — "summarized" or "omitted".

    The API default is "omitted", which streams empty thinking blocks and
    reads as a long pause before any output. We ask for summaries so the
    `THINKING_DELTA` event has something to carry.
    """


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
        default_effort: str = _DEFAULT_EFFORT,
        thinking_effort: str = _THINKING_EFFORT,
    ) -> None:
        self._default_model = default_model
        self._thinking_model = thinking_model
        self._default_max_tokens = default_max_tokens
        self._thinking_max_tokens = thinking_max_tokens
        self._default_effort = default_effort
        self._thinking_effort = thinking_effort

    def pick(
        self,
        *,
        use_extended_thinking: bool = False,
        override_model: str | None = None,
    ) -> ModelChoice:
        """Resolve a `ContinueRequest` to a concrete model + knobs.

        Precedence:
          1. `override_model` (caller forced a specific id)
          2. `use_extended_thinking=True` -> Opus at high effort
          3. default Sonnet at medium effort
        """
        if override_model:
            return ModelChoice(
                model=override_model,
                max_tokens=self._default_max_tokens,
                effort=self._default_effort,
            )
        if use_extended_thinking:
            return ModelChoice(
                model=self._thinking_model,
                max_tokens=self._thinking_max_tokens,
                effort=self._thinking_effort,
            )
        return ModelChoice(
            model=self._default_model,
            max_tokens=self._default_max_tokens,
            effort=self._default_effort,
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
