"""
EMA Crossover — TA-3.5 baseline.

Trend-following baseline using EMA (exponential MA) instead of
SMA. EMA weights recent prices more heavily, so the crossover
signal:
  - fires earlier than the SMA equivalent on real moves,
  - whips more often on chop (false signals during ranges).

Direct A/B vs `sma_crossover`: same parameters (fast/slow
periods), same long-only mechanics, same fees/slippage. The
only difference is the MA family. Side-by-side measures the
EMA-vs-SMA tradeoff on the same window.

Modularity contract: pure strategy. Indicators reached via
`ctx.indicator("ema", period=N)` — never imports the EMA class
directly. Same modularity rules as `sma_crossover.py`.
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


class EmaCrossoverParams(BaseModel):
    fast_period: int = Field(12, ge=2, le=500)
    slow_period: int = Field(26, ge=3, le=500)
    position_size_pct: float = Field(0.95, gt=0.0, le=1.0)

    def validate_periods(self) -> None:
        if self.fast_period >= self.slow_period:
            raise ValueError(
                f"fast_period ({self.fast_period}) must be < slow_period "
                f"({self.slow_period}) — otherwise the cross signal degenerates."
            )


class EmaCrossoverStrategy(BaseStrategy):
    """
    Long-only EMA crossover on a single symbol.

    Defaults of 12/26 mirror MACD's fast/slow EMAs (canonical
    momentum periods). Use 9/21 for a faster signal, 50/200 for
    a slower "golden cross" style.

    Cross detection requires both fast/slow EMAs to have valid
    (non-NaN) latest AND previous values — once warmup is past,
    we look for the bar where fast crosses slow.

      Cross up:  fast_prev <= slow_prev AND fast_now > slow_now → BUY (when flat)
      Cross down: fast_prev >= slow_prev AND fast_now < slow_now → SELL (when long)
    """

    name: str = "ema_crossover"
    version: str = "0.1"

    def __init__(
        self,
        params: Optional[EmaCrossoverParams] = None,
        *,
        interval: str = "1d",
    ) -> None:
        self.params = params or EmaCrossoverParams()
        self.params.validate_periods()
        self.interval = interval

    def on_bar(self, ctx: Context) -> Action:
        p = self.params

        # Need enough history for the SLOW EMA to produce two values
        # (one current, one previous — required for cross detection).
        if len(ctx.history) < p.slow_period + 1:
            return hold()

        fast_series = ctx.indicator("ema", period=p.fast_period)
        slow_series = ctx.indicator("ema", period=p.slow_period)

        fast_now, fast_prev = _last_two(fast_series)
        slow_now, slow_prev = _last_two(slow_series)
        if any(_isnan(v) for v in (fast_now, fast_prev, slow_now, slow_prev)):
            return hold()

        symbol = ctx.bar.symbol
        position = ctx.portfolio.positions.get(symbol)
        has_position = position is not None and position.quantity > 0

        cross_up = fast_prev <= slow_prev and fast_now > slow_now
        cross_down = fast_prev >= slow_prev and fast_now < slow_now

        if cross_up and not has_position:
            price = ctx.bar.close
            if price <= 0:
                return hold()
            cash_to_spend = ctx.portfolio.cash * p.position_size_pct
            qty = math.floor(cash_to_spend / price)
            if qty <= 0:
                return hold()
            ctx.log(
                event="signal_buy",
                fast=fast_now, slow=slow_now, price=price, qty=qty,
            )
            return Action(
                kind="buy", symbol=symbol, size=float(qty),
                note=f"ema_crossover up @ fast={fast_now:.4f} slow={slow_now:.4f}",
            )

        if cross_down and has_position:
            qty = position.quantity
            ctx.log(
                event="signal_sell",
                fast=fast_now, slow=slow_now, qty=qty,
            )
            return Action(
                kind="sell", symbol=symbol, size=qty,
                note=f"ema_crossover down @ fast={fast_now:.4f} slow={slow_now:.4f}",
            )

        return hold()


def _last_two(series: pd.Series) -> tuple[float, float]:
    if len(series) < 2:
        return (float("nan"), float("nan"))
    return (float(series.iloc[-1]), float(series.iloc[-2]))


def _isnan(v: float) -> bool:
    return v != v
