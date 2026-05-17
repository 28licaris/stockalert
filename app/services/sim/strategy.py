"""
Strategy Protocol + BaseStrategy convenience class.

Every strategy (rule-based, LLM-driven, RL-trained) implements the
same `Strategy` Protocol — that's how the backtester accepts any of
them interchangeably. `BaseStrategy` provides sensible defaults for
the `setup` / `teardown` lifecycle so simple rule-based strategies
only need to write `on_bar`.

Strategies are **pure**: same Context → same Action. The
`test_strategy_is_pure` structural gate (added in TA-1's test slice)
enforces that strategy modules don't import `app.db.*`,
`app.providers.*`, or network libs.
"""
from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

from app.services.sim.context import Context
from app.services.sim.schemas import Action, hold

logger = logging.getLogger(__name__)


@runtime_checkable
class Strategy(Protocol):
    """
    Anything that can serve as a backtest strategy.

    Required attributes:
      - `name: str`        — serializable identifier, recorded in agent_runs.
      - `version: str`     — bump on logic change; invalidates result caches.
      - `interval: str`    — required bar interval ('1d', '1h', '5m', ...).
        For single-timeframe strategies this is the only one.
        For multi-timeframe strategies, this is the EXECUTION interval
        (the finest one the harness iterates on); the full list lives
        in the optional `intervals` attribute below.

    Optional multi-timeframe attribute:
      - `intervals: list[str]` (coarsest-to-finest) — declare additional
        contextual intervals beyond `interval`. The backtester fetches
        bars at each, the Context exposes them via `history_at(interval)`
        and `indicator(name, interval=..., **params)`. If absent, the
        harness treats the strategy as single-TF on `interval`.

    Lifecycle:
      - `setup(ctx)`   — once before the run.
      - `on_bar(ctx)`  — once per execution bar. Returns one Action.
      - `teardown(ctx)` — once after the run.

    Implementation note: this is a `Protocol` (duck-typed), not an
    ABC. LLM- and RL-driven strategies don't naturally fit Python's
    class hierarchy; `on_bar` is the only contract. `BaseStrategy`
    is convenience inheritance for rule-based strategies.
    """

    name: str
    version: str
    interval: str

    def setup(self, ctx: Context) -> None: ...
    def on_bar(self, ctx: Context) -> Action: ...
    def teardown(self, ctx: Context) -> None: ...


def required_intervals(strategy: Strategy) -> list[str]:
    """
    Return the list of intervals a strategy requires the backtester
    to fetch.

    Multi-TF strategies declare `intervals: list[str]` (coarsest-to-
    finest). Single-TF strategies declare only `interval: str`, which
    we wrap into a single-element list.

    The execution interval is always `intervals[-1]` after this call.
    """
    declared = getattr(strategy, "intervals", None)
    if declared:
        return list(declared)
    return [strategy.interval]


class BaseStrategy:
    """
    Convenience base for rule-based strategies. Implements no-op
    setup/teardown so concrete strategies only need to define
    `on_bar`. LLM/RL strategies typically skip this and implement
    the Protocol directly.

    Concrete strategies should:
      - set `name`, `version`, `interval` as class attributes
      - accept their Pydantic `Params` in `__init__`
      - implement `on_bar(self, ctx) -> Action`
    """

    name: str = "base"
    version: str = "0.0"
    interval: str = "1d"

    def setup(self, ctx: Context) -> None:
        """Default: no-op. Override to allocate strategy-local state."""

    def on_bar(self, ctx: Context) -> Action:
        """Default: hold. Override to emit real decisions."""
        return hold()

    def teardown(self, ctx: Context) -> None:
        """Default: no-op. Override to release resources (LLM sessions, etc)."""

    def params_dict(self) -> dict[str, Any]:
        """
        Serialize strategy params for the agent_runs registry.

        Default: introspect for a `params` attribute that's a Pydantic
        model. Override if the strategy stores params elsewhere.
        """
        params = getattr(self, "params", None)
        if params is None:
            return {}
        if hasattr(params, "model_dump"):
            return params.model_dump(mode="json")
        if isinstance(params, dict):
            return dict(params)
        return {}
