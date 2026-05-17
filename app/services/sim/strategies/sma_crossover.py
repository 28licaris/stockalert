"""
SMA Crossover — the TA-1 canary strategy.

Classic "fast MA crosses slow MA" rule:
  - When fast SMA crosses ABOVE slow SMA  -> go long full position.
  - When fast SMA crosses BELOW slow SMA  -> exit position.

Purpose: validate that the harness works end-to-end. NOT for
production trading — SMA crossovers are a known mediocre signal
and are here only to exercise the framework with a deterministic
strategy that produces visible trades on common stocks.

Implementation notes for future strategy authors:
  - Strategy state (last MA values, last cross direction) lives in
    instance attrs — strategies own their state, the harness doesn't.
  - Indicators accessed via `ctx.indicator("sma", period=N)` — never
    by importing `SMA` directly. The indicator registry is the
    single source of truth for name→class.
  - `position_size_pct=0.95` is the standard "buy with 95% of cash"
    heuristic. We deliberately leave ~5% as a buffer for fees +
    slippage; otherwise the first buy can fail to fill.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

import pandas as pd
from pydantic import BaseModel, Field

from app.services.sim.context import Context
from app.services.sim.schemas import Action, hold
from app.services.sim.strategy import BaseStrategy

logger = logging.getLogger(__name__)


class SmaCrossoverParams(BaseModel):
    fast_period: int = Field(20, ge=2, le=500)
    slow_period: int = Field(50, ge=3, le=500)
    position_size_pct: float = Field(
        0.95, gt=0.0, le=1.0,
        description="Fraction of cash deployed on entry. 95% leaves headroom for fees + slippage.",
    )

    def validate_periods(self) -> None:
        if self.fast_period >= self.slow_period:
            raise ValueError(
                f"fast_period ({self.fast_period}) must be < slow_period "
                f"({self.slow_period}) — otherwise the cross signal degenerates."
            )


class SmaCrossoverStrategy(BaseStrategy):
    """
    Long-only SMA crossover on a single symbol.

    On every bar:
      1. Compute fast SMA and slow SMA.
      2. If both have valid (non-NaN) latest values, compare to
         previous values to detect a cross.
      3. Cross UP (fast > slow, was fast <= slow last bar) AND no
         position -> emit BUY for `position_size_pct * cash / price`.
      4. Cross DOWN (fast < slow, was fast >= slow last bar) AND
         have position -> emit SELL for full quantity.
      5. Otherwise hold.

    The crossover logic is **interval-agnostic** — the same code runs
    on daily, hourly, 5-minute, or 1-minute bars. The `interval`
    constructor argument declares which bar type the strategy expects
    from the harness (the Backtester validates the match).
    """

    name: str = "sma_crossover"
    version: str = "0.1"

    def __init__(
        self,
        params: Optional[SmaCrossoverParams] = None,
        *,
        interval: str = "1d",
    ) -> None:
        self.params = params or SmaCrossoverParams()
        self.params.validate_periods()
        self.interval = interval  # instance attr overrides class default
        # State maintained across on_bar calls (strategy-owned).
        self._prev_fast: float | None = None
        self._prev_slow: float | None = None

    def setup(self, ctx: Context) -> None:
        self._prev_fast = None
        self._prev_slow = None

    def on_bar(self, ctx: Context) -> Action:
        p = self.params

        # Need at least slow_period+1 bars to detect the FIRST cross
        # (need one bar of "previous" indicator values plus a current value).
        if len(ctx.history) < p.slow_period + 1:
            return hold()

        fast_series = ctx.indicator("sma", period=p.fast_period)
        slow_series = ctx.indicator("sma", period=p.slow_period)

        fast_now, fast_prev = _last_two(fast_series)
        slow_now, slow_prev = _last_two(slow_series)
        if any(_isnan(v) for v in (fast_now, fast_prev, slow_now, slow_prev)):
            return hold()

        symbol = ctx.bar.symbol
        position_qty = ctx.portfolio.positions.get(symbol, None)
        has_position = position_qty is not None and position_qty.quantity > 0

        cross_up = fast_prev <= slow_prev and fast_now > slow_now
        cross_down = fast_prev >= slow_prev and fast_now < slow_now

        if cross_up and not has_position:
            # Size: spend position_size_pct of cash at the CURRENT bar's
            # close as a price proxy. The actual fill happens at the
            # next bar's open (per SlippageModel default), so the qty
            # may end up slightly different — that's fine for a backtest.
            price = ctx.bar.close
            if price <= 0:
                return hold()
            cash_to_spend = ctx.portfolio.cash * p.position_size_pct
            qty = math.floor(cash_to_spend / price)  # integer shares for realism
            if qty <= 0:
                return hold()
            ctx.log(
                event="signal_buy",
                fast=fast_now, slow=slow_now, price=price, qty=qty,
            )
            return Action(
                kind="buy", symbol=symbol, size=float(qty),
                note=f"sma_crossover up @ fast={fast_now:.4f} slow={slow_now:.4f}",
            )

        if cross_down and has_position:
            qty = position_qty.quantity
            ctx.log(
                event="signal_sell",
                fast=fast_now, slow=slow_now, qty=qty,
            )
            return Action(
                kind="sell", symbol=symbol, size=qty,
                note=f"sma_crossover down @ fast={fast_now:.4f} slow={slow_now:.4f}",
            )

        return hold()


# ─────────────────────────────────────────────────────────────────────
# Helpers (module-private)
# ─────────────────────────────────────────────────────────────────────


def _last_two(series: pd.Series) -> tuple[float, float]:
    """Last two values of a Series, as floats. Returns (nan, nan) if too short."""
    if len(series) < 2:
        return (float("nan"), float("nan"))
    return (float(series.iloc[-1]), float(series.iloc[-2]))


def _isnan(v: float) -> bool:
    return v != v  # NaN check without importing math here
