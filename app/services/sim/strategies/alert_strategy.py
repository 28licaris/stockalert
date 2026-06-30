"""
AlertStrategy — the signal→backtest bridge (Milestone 1).

Trades any `SignalSource` uniformly: on each bar, if flat, ask the source for a
setup and (if it passes the gates) enter it sized off its stop; if in a
position, exit when the bar touches the stop or target. This is what finally
makes our alerts/setups *backtestable* — point it at `ma_cross`, `breakout`,
or (later) an Elliott-Wave source and get reproducible win-rate / P&L / drawdown
from the existing evaluator + agent_runs registry.

Design choices (see docs/strategy_rnd_platform_design.md):
  - **Long-only** for now (the engine reserves quantity<0 for a later short tier).
    Short signals are skipped.
  - **Risk-based sizing:** size so entry→stop loss ≈ `risk_pct` of equity, capped
    by available cash. This makes win-rate and avg-R comparable across signals.
  - **Exit fills** are market orders emitted on the bar that touches stop/target,
    filled at the next bar's open per the harness slippage model. This slightly
    flatters/penalizes vs a true stop fill at the stop price; it's the honest,
    no-look-ahead baseline. A limit/stop fill model is a later refinement.
  - **Gates:** minimum reward:risk and confidence — the seed of the Milestone-2
    A+ scoring layer.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

from pydantic import BaseModel, Field

from app.services.sim.context import Context
from app.services.sim.filters import FilteredSignalSource, build_filter
from app.services.sim.schemas import Action, hold
from app.services.sim.signal_source import Signal, build_signal_source
from app.services.sim.strategy import BaseStrategy

logger = logging.getLogger(__name__)


class AlertStrategyParams(BaseModel):
    source: str = Field("breakout", description="Registered SignalSource name (ma_cross, breakout).")
    source_params: dict = Field(default_factory=dict, description="kwargs for the SignalSource.")
    filters: list[dict] = Field(
        default_factory=list,
        description=(
            "Composable A+ filters applied to each signal, e.g. "
            "[{name: trend, params: {period: 50}}, {name: reward_risk, params: {min_rr: 2}}]. "
            "Empty = raw source signals."
        ),
    )
    filter_mode: str = Field(
        "all", description="'all' (every filter must pass) or 'score' (weighted score ≥ min_score).",
    )
    min_score: Optional[float] = Field(
        None, description="For filter_mode='score': minimum summed filter weight to pass.",
    )
    risk_pct: float = Field(
        0.01, gt=0.0, le=0.25,
        description="Base fraction of equity risked per trade (entry→stop). 0.01 = 1%.",
    )
    max_risk_pct: Optional[float] = Field(
        None, gt=0.0, le=0.25,
        description=(
            "Ceiling risk for FULL-confluence (confidence=1.0) setups; risk scales "
            "linearly from risk_pct (confidence 0) to max_risk_pct (confidence 1). "
            "e.g. risk_pct=0.01, max_risk_pct=0.05 → 1% on weak setups, up to 5% when "
            "every confirmation agrees. None = flat risk_pct (no scaling)."
        ),
    )
    max_cash_pct: float = Field(
        0.95, gt=0.0, le=1.0,
        description="Cap on cash deployed per position (headroom for fees/slippage).",
    )
    min_reward_risk: float = Field(
        1.5, ge=0.0, description="Skip signals whose target:stop reward:risk is below this.",
    )
    min_confidence: float = Field(
        0.0, ge=0.0, le=1.0, description="Skip signals below this source confidence.",
    )
    max_holding_days: Optional[float] = Field(
        None,
        description=(
            "Time stop: exit at market once a position has been held this many "
            "calendar days, even if neither stop nor target hit. None = no time "
            "stop (hold until stop/target). Caps capital tied up per trade."
        ),
    )


class AlertStrategy(BaseStrategy):
    """Execute a pluggable SignalSource as long trades with stop/target exits."""

    name: str = "alert_driven"
    version: str = "0.1"

    def __init__(
        self,
        params: Optional[AlertStrategyParams] = None,
        *,
        interval: str = "1d",
    ) -> None:
        self.params = params or AlertStrategyParams()
        self.interval = interval
        base = build_signal_source(self.params.source, **self.params.source_params)
        # Wrap in the composable A+ filter layer when filters are declared.
        if self.params.filters:
            built = [build_filter(f["name"], **(f.get("params") or {})) for f in self.params.filters]
            self.source = FilteredSignalSource(
                base, built, mode=self.params.filter_mode, min_score=self.params.min_score,
            )
        else:
            self.source = base
        # Active trade plan per symbol (stop/target tracking for open positions).
        self._plans: dict[str, Signal] = {}

    def setup(self, ctx: Context) -> None:
        self._plans = {}
        self.source.setup(ctx)

    def on_bar(self, ctx: Context) -> Action:
        symbol = ctx.bar.symbol
        pos = ctx.portfolio.positions.get(symbol)
        has_position = pos is not None and pos.quantity > 0

        if has_position:
            return self._manage_exit(ctx, symbol, pos.quantity)

        return self._maybe_enter(ctx, symbol)

    # ── exits ──────────────────────────────────────────────────────────
    def _manage_exit(self, ctx: Context, symbol: str, qty: float) -> Action:
        plan = self._plans.get(symbol)
        if plan is None:
            return hold()  # position without a tracked plan — leave it be
        bar = ctx.bar
        # Stop takes priority over target when a single bar spans both (worst case).
        if bar.low <= plan.stop:
            self._plans.pop(symbol, None)
            ctx.log(event="exit_stop", stop=plan.stop, low=bar.low)
            return Action(kind="sell", symbol=symbol, size=qty,
                          note=f"stop hit @ {plan.stop:.4f} ({plan.kind})")
        if bar.high >= plan.target_1:
            self._plans.pop(symbol, None)
            ctx.log(event="exit_target", target=plan.target_1, high=bar.high)
            return Action(kind="sell", symbol=symbol, size=qty,
                          note=f"target hit @ {plan.target_1:.4f} ({plan.kind})")
        # Time stop — cap how long capital stays tied up.
        if self.params.max_holding_days is not None:
            pos = ctx.portfolio.positions.get(symbol)
            if pos is not None:
                held = (bar.timestamp - pos.entry_time).total_seconds() / 86_400.0
                if held >= self.params.max_holding_days:
                    self._plans.pop(symbol, None)
                    ctx.log(event="exit_time", held_days=round(held, 1))
                    return Action(kind="sell", symbol=symbol, size=qty,
                                  note=f"time stop @ {held:.0f}d ({plan.kind})")
        return hold()

    # ── entries ────────────────────────────────────────────────────────
    def _maybe_enter(self, ctx: Context, symbol: str) -> Action:
        sig = self.source.on_bar(ctx)
        if sig is None or sig.direction != "long":
            return hold()
        if sig.risk_per_share <= 0:
            return hold()
        if sig.reward_risk < self.params.min_reward_risk:
            return hold()
        if sig.confidence < self.params.min_confidence:
            return hold()

        qty = self._size(ctx, sig)
        if qty <= 0:
            return hold()
        self._plans[symbol] = sig
        ctx.log(
            event="entry", kind=sig.kind, entry=sig.entry,
            stop=sig.stop, target=sig.target_1, qty=qty, rr=round(sig.reward_risk, 2),
        )
        return Action(kind="buy", symbol=symbol, size=float(qty),
                      note=f"{sig.kind} entry rr={sig.reward_risk:.2f}: {sig.rationale}")

    def _size(self, ctx: Context, sig: Signal) -> int:
        """Conviction-scaled risk size: risk scales risk_pct→max_risk_pct by the
        signal's confluence confidence, then converts to shares off the stop,
        capped by cash. Higher-probability (more-confirmed) setups bet more."""
        p = self.params
        ceil = p.max_risk_pct if p.max_risk_pct is not None else p.risk_pct
        conf = min(1.0, max(0.0, sig.confidence))
        risk_pct = p.risk_pct + (ceil - p.risk_pct) * conf
        risk_amount = ctx.portfolio.equity * risk_pct
        qty_by_risk = math.floor(risk_amount / sig.risk_per_share)
        cash_cap = ctx.portfolio.cash * p.max_cash_pct
        qty_by_cash = math.floor(cash_cap / sig.entry) if sig.entry > 0 else 0
        return max(0, min(qty_by_risk, qty_by_cash))
