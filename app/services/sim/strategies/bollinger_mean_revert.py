"""
Bollinger Mean-Revert — TA-3.4 mean-revert baseline.

Different mean-revert mechanic from RSI extreme reversion: this
one uses a VOLATILITY ENVELOPE (Bollinger Bands) rather than a
threshold on an oscillator. The hypothesis: when price touches
the lower band, the security is statistically two standard
deviations below its 20-day mean — a snap-back is more likely
than not. Exit when price reverts to the middle band (the SMA).

Concrete rules:
  - Long when close <= lower_band AND no position. (Strict
    inequality would miss exact-touch bars; use <=.)
  - Exit when close >= middle_band AND have position.
    (The "first hold" exit — close once we're back to fair.)
  - Otherwise hold.

This strategy is **complementary** to `rsi_reversion`:
  - RSI Reversion fires when momentum (rate of change) is
    stretched. Doesn't know about absolute price level.
  - Bollinger Mean-Revert fires when absolute deviation from
    a moving average is stretched. Doesn't know about momentum.

Different signals on the same window means different trade
counts and different PnL profiles — that's why both are
useful baselines for the LLM agent to beat.

Modularity contract: pure function of price + indicators.
Indicators accessed by name via Context; no direct import of
`BollingerBands`. Strategy file imports nothing from
`app.db.*` / `app.providers.*` (gated by `test_strategy_is_pure`).
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


class BollingerMeanRevertParams(BaseModel):
    period: int = Field(
        20, ge=2, le=500,
        description="Bollinger lookback. Classic: 20.",
    )
    std_multiplier: float = Field(
        2.0, gt=0.0,
        description=(
            "Band width in stdev multiples. Classic: 2.0. Looser "
            "(2.5+) waits for bigger dislocations; tighter (1.5) "
            "fires more often."
        ),
    )
    position_size_pct: float = Field(
        0.95, gt=0.0, le=1.0,
        description="Fraction of cash to deploy on entry.",
    )


class BollingerMeanRevertStrategy(BaseStrategy):
    """
    Long-only Bollinger-band mean-reversion strategy.

    Entry: `close[-1] <= lower_band[-1]` (touch or breach of lower
    band) AND flat → BUY floor(cash * pct / price) shares.

    Exit: `close[-1] >= middle_band[-1]` (reverted to the SMA
    midline) AND long → SELL full position.

    Otherwise hold.

    Note: this is **NOT** a "wait for bounce confirmation" variant
    — we enter on the touch, not on the first up-bar after a touch.
    Confirmation variants are easy follow-ups; this version is the
    simplest mean-revert hypothesis test.
    """

    name: str = "bollinger_mean_revert"
    version: str = "0.1"

    def __init__(
        self,
        params: Optional[BollingerMeanRevertParams] = None,
        *,
        interval: str = "1d",
    ) -> None:
        self.params = params or BollingerMeanRevertParams()
        self.interval = interval

    def on_bar(self, ctx: Context) -> Action:
        p = self.params

        # Warmup: Bollinger Bands need `period` bars in history
        # before the rolling std and rolling mean are valid.
        if len(ctx.history) < p.period + 1:
            return hold()

        # ctx.indicator returns the canonical (middle band / SMA)
        # series. For mean-revert we need upper/lower too — so we
        # request them by name via the same registry the IndicatorReader
        # uses on the exposure side. The naming convention:
        # `bollinger_lower`, `bollinger_middle`, `bollinger_upper`.
        # Strategy accesses via the same lazy-cache mechanism.
        upper, middle, lower = self._bands(ctx)
        if upper is None or middle is None or lower is None:
            return hold()

        latest_close = float(ctx.bar.close)
        latest_middle = float(middle.iloc[-1])
        latest_lower = float(lower.iloc[-1])

        if _isnan(latest_close) or _isnan(latest_middle) or _isnan(latest_lower):
            return hold()

        symbol = ctx.bar.symbol
        position = ctx.portfolio.positions.get(symbol)
        has_position = position is not None and position.quantity > 0

        # Entry: close at or below lower band, no position.
        if not has_position and latest_close <= latest_lower:
            price = latest_close
            if price <= 0:
                return hold()
            cash_to_spend = ctx.portfolio.cash * p.position_size_pct
            qty = math.floor(cash_to_spend / price)
            if qty <= 0:
                return hold()
            ctx.log(
                event="signal_buy",
                close=latest_close, lower=latest_lower, middle=latest_middle, qty=qty,
            )
            return Action(
                kind="buy", symbol=symbol, size=float(qty),
                note=f"bb_mean_revert: close={latest_close:.2f} <= lower={latest_lower:.2f}",
            )

        # Exit: close back at or above middle (reverted to mean), long.
        if has_position and latest_close >= latest_middle:
            qty = position.quantity
            ctx.log(
                event="signal_sell",
                close=latest_close, middle=latest_middle, qty=qty,
            )
            return Action(
                kind="sell", symbol=symbol, size=qty,
                note=f"bb_mean_revert: close={latest_close:.2f} >= middle={latest_middle:.2f}",
            )

        return hold()

    # ─────────────────────────────────────────────────────────────────
    # Internals
    # ─────────────────────────────────────────────────────────────────

    def _bands(
        self, ctx: Context,
    ) -> tuple[Optional[pd.Series], Optional[pd.Series], Optional[pd.Series]]:
        """
        Compute Bollinger bands via the Context's indicator API.

        We compute the SMA midline via `ctx.indicator("sma", period=...)`
        and the rolling stdev via a tiny direct call on the history
        DataFrame. This avoids needing a "bollinger_lower"-named
        registry entry while still using the same SMA computation
        that the indicator registry produces (single source of truth).

        For the exposure layer that DOES want named bollinger_*
        series, the `IndicatorReader` uses `BollingerBands.compute_full`
        directly — see `app/services/readers/indicator_reader.py`.
        """
        p = self.params
        try:
            middle = ctx.indicator("sma", period=p.period)
        except Exception as exc:  # noqa: BLE001 — degrade to hold
            logger.warning("BollingerMeanRevert: SMA midline failed: %s", exc)
            return (None, None, None)

        df = ctx.history.to_dataframe()
        if df.empty or "close" not in df.columns:
            return (None, None, None)

        # ddof=0 matches BollingerBands.compute_full and every charting
        # platform. The bands here are guaranteed identical to what the
        # exposure-layer IndicatorReader emits via `bollinger_*`.
        rolling_std = df["close"].rolling(
            window=p.period, min_periods=p.period,
        ).std(ddof=0)
        offset = rolling_std * p.std_multiplier
        upper = middle + offset
        lower = middle - offset
        return (upper, middle, lower)


def _isnan(v: float) -> bool:
    return v != v
