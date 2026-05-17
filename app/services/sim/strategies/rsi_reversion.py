"""
RSI Extreme Reversion — TA-3.3 mean-revert baseline.

Classic oversold-bounce hypothesis: when momentum gets stretched
below `oversold_threshold` (commonly RSI < 30), the security has
overshot and a snap-back bounce is more likely than not. We go
long. We exit when RSI recovers past `exit_threshold` (commonly
50) — indicating momentum has neutralized and the bounce has
played out.

Purpose: comparison baseline alongside SMA Crossover. Mean-revert
strategies behave very differently from trend-following ones —
they win on chop and lose on trend. Side-by-side bake-off
(TA-3.6) measures both characteristics on the same window.

Not for production trading on its own — bare RSI mean-revert
gets killed in strong trends ("falling knife"). Realistic
deployment pairs this with a trend filter (e.g. only take the
signal when price > SMA(200)).

Modularity contract (per `feedback_trading_subsystem_design`):
- Pure function of (price + indicators). No app.db.* / providers.
- Indicators accessed by name through Context — never via direct
  import of the RSI class.
- Interval-agnostic; the constructor takes `interval` so the
  same logic runs on daily, hourly, 5-minute bars.
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


class RsiReversionParams(BaseModel):
    rsi_period: int = Field(14, ge=2, le=200)
    oversold_threshold: float = Field(
        30.0,
        ge=0.0, le=100.0,
        description="Enter long when RSI drops strictly below this. Classic: 30 (loose), 25 (tight), 20 (rare).",
    )
    exit_threshold: float = Field(
        50.0,
        ge=0.0, le=100.0,
        description=(
            "Exit when RSI rises strictly above this. Classic: 50 (neutral) or "
            "70 (ride into overbought; risk-loving variant)."
        ),
    )
    position_size_pct: float = Field(
        0.95,
        gt=0.0, le=1.0,
        description="Fraction of cash to deploy on entry. 95% leaves headroom for fees + slippage.",
    )

    def validate_thresholds(self) -> None:
        if self.oversold_threshold >= self.exit_threshold:
            raise ValueError(
                f"oversold_threshold ({self.oversold_threshold}) must be < "
                f"exit_threshold ({self.exit_threshold}) — otherwise the strategy "
                "would buy and sell simultaneously."
            )


class RsiReversionStrategy(BaseStrategy):
    """
    Long-only RSI extreme-reversion strategy.

    Loop:
      1. Compute RSI(period). Skip until non-NaN.
      2. If no position AND latest RSI < oversold_threshold → BUY.
      3. If position AND latest RSI > exit_threshold → SELL all.
      4. Otherwise hold.

    NOT a cross-detection variant — we don't require RSI to PASS
    through the threshold this bar. As long as RSI is below
    `oversold_threshold` and we're flat, we'll enter on the next
    bar. This is more robust to noisy intraday RSI dips than a
    strict cross trigger, and matches the spirit of "enter while
    oversold" rather than "enter exactly when the dip starts."
    """

    name: str = "rsi_reversion"
    version: str = "0.1"

    def __init__(
        self,
        params: Optional[RsiReversionParams] = None,
        *,
        interval: str = "1d",
    ) -> None:
        self.params = params or RsiReversionParams()
        self.params.validate_thresholds()
        self.interval = interval

    def on_bar(self, ctx: Context) -> Action:
        p = self.params

        # Warmup: need enough history for RSI to produce a value.
        # Wilder's RSI(n) needs n+1 close-to-close differences, i.e.
        # n+2 bars in history.
        if len(ctx.history) < p.rsi_period + 2:
            return hold()

        rsi_series = ctx.indicator("rsi", period=p.rsi_period)
        if len(rsi_series) == 0:
            return hold()
        latest = float(rsi_series.iloc[-1])
        if _isnan(latest):
            return hold()

        symbol = ctx.bar.symbol
        position = ctx.portfolio.positions.get(symbol)
        has_position = position is not None and position.quantity > 0

        if not has_position and latest < p.oversold_threshold:
            price = ctx.bar.close
            if price <= 0:
                return hold()
            cash_to_spend = ctx.portfolio.cash * p.position_size_pct
            qty = math.floor(cash_to_spend / price)
            if qty <= 0:
                return hold()
            ctx.log(event="signal_buy", rsi=latest, threshold=p.oversold_threshold, qty=qty)
            return Action(
                kind="buy", symbol=symbol, size=float(qty),
                note=f"rsi_reversion: rsi={latest:.2f} < {p.oversold_threshold}",
            )

        if has_position and latest > p.exit_threshold:
            qty = position.quantity
            ctx.log(event="signal_sell", rsi=latest, threshold=p.exit_threshold, qty=qty)
            return Action(
                kind="sell", symbol=symbol, size=qty,
                note=f"rsi_reversion: rsi={latest:.2f} > {p.exit_threshold}",
            )

        return hold()


def _isnan(v: float) -> bool:
    return v != v
