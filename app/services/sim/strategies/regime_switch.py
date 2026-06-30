"""
RegimeSwitchStrategy — trade different setups depending on the market regime.

The synthesis of EXP-14: confluence-gated breakout is excellent in up-regimes but
should stand aside (or flip) when the broad market rolls over. This strategy reads
the benchmark regime (SPY above/below its regime MA) once per bar and routes to:

  - up-regime  → the `up` branch   (e.g. confluence-gated breakout long)
  - down-regime → the `down` branch (e.g. reversal shorts) — or CASH if `down`
                  is omitted (only manage open exits; take no new entries).

It reuses AlertStrategy's sizing / stop-target / time-stop exit machinery verbatim
— the ONLY thing that changes per bar is which SignalSource is asked for an entry.
Regime is evaluated as-of the current bar (no look-ahead) via `ctx.market`.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.services.sim.context import Context
from app.services.sim.schemas import Action, hold
from app.services.sim.strategies.alert_strategy import (
    AlertStrategy,
    AlertStrategyParams,
    build_filtered_source,
)


class RegimeBranch(BaseModel):
    """One regime's setup: a signal source + optional A+ filter stack."""
    source: str = "breakout"
    source_params: dict = Field(default_factory=dict)
    filters: list[dict] = Field(default_factory=list)
    filter_mode: str = "all"
    min_score: Optional[float] = None


class RegimeSwitchParams(BaseModel):
    regime_ma_period: int = Field(
        100, ge=2, description="Benchmark SMA period defining up vs down regime.",
    )
    up: RegimeBranch = Field(default_factory=RegimeBranch)
    down: Optional[RegimeBranch] = Field(
        None, description="Down-regime branch; None = go to cash (manage exits only).",
    )
    # Shared risk / gates (same semantics as AlertStrategyParams).
    risk_pct: float = Field(0.01, gt=0.0, le=0.25)
    max_risk_pct: Optional[float] = Field(None, gt=0.0, le=0.25)
    max_cash_pct: float = Field(0.95, gt=0.0, le=1.0)
    min_reward_risk: float = Field(1.5, ge=0.0)
    min_confidence: float = Field(0.0, ge=0.0, le=1.0)
    max_holding_days: Optional[float] = None


class RegimeSwitchStrategy(AlertStrategy):
    """Route between an up-regime and down-regime SignalSource by benchmark trend."""

    name: str = "regime_switch"
    version: str = "0.1"

    def __init__(self, params: Optional[RegimeSwitchParams] = None, *, interval: str = "1d") -> None:
        self.rs = params or RegimeSwitchParams()
        self.interval = interval
        # Reuse AlertStrategy's risk/gate machinery by mirroring the shared fields.
        self.params = AlertStrategyParams(
            source=self.rs.up.source, source_params=self.rs.up.source_params,
            risk_pct=self.rs.risk_pct, max_risk_pct=self.rs.max_risk_pct,
            max_cash_pct=self.rs.max_cash_pct, min_reward_risk=self.rs.min_reward_risk,
            min_confidence=self.rs.min_confidence, max_holding_days=self.rs.max_holding_days,
        )
        self.up_source = build_filtered_source(
            self.rs.up.source, self.rs.up.source_params, self.rs.up.filters,
            filter_mode=self.rs.up.filter_mode, min_score=self.rs.up.min_score,
        )
        self.down_source = (
            build_filtered_source(
                self.rs.down.source, self.rs.down.source_params, self.rs.down.filters,
                filter_mode=self.rs.down.filter_mode, min_score=self.rs.down.min_score,
            )
            if self.rs.down is not None else None
        )
        self.source = self.up_source  # default; reset per bar in on_bar
        self._plans = {}

    def setup(self, ctx: Context) -> None:
        self._plans = {}
        self.up_source.setup(ctx)
        if self.down_source is not None:
            self.down_source.setup(ctx)

    def _up_regime(self, ctx: Context) -> bool:
        """SPY (or configured benchmark) above its regime MA, as-of this bar.
        No benchmark / warmup → default to up-regime (trade the long branch)."""
        mc = getattr(ctx, "market", None)
        if mc is None:
            return True
        above = mc.above_ma_asof(ctx.bar.timestamp, self.rs.regime_ma_period)
        return True if above is None else bool(above)

    def on_bar(self, ctx: Context) -> Action:
        # Pick the active source for entries based on regime; exits are
        # regime-independent (handled by AlertStrategy via self._plans).
        active = self.up_source if self._up_regime(ctx) else self.down_source
        if active is None:
            symbol = ctx.bar.symbol
            pos = ctx.portfolio.positions.get(symbol)
            if pos is not None and pos.quantity != 0:
                return self._manage_exit(ctx, symbol, pos)
            return hold()  # down-regime + cash branch: no new entries
        self.source = active
        return super().on_bar(ctx)
