"""
Multi-Timeframe EMA Crossover with Daily Trend Filter — TA-4.2.

The canonical swing-trade pattern: regime check on a SLOW
timeframe (daily), execution on a FAST timeframe (hourly).

Logic per hourly bar:
  1. **Regime gate (daily):** is the most recent CLOSED daily bar
     above its `daily_trend_period` SMA? If not, the broader trend
     is not up — stay flat regardless of any hourly signal.
  2. **Entry (hourly):** if daily trend is up AND fast EMA crosses
     above slow EMA on the hourly AND we're flat → BUY.
  3. **Exit (hourly):** if we hold a position AND fast EMA crosses
     below slow EMA on hourly → SELL (close regardless of regime —
     respect the exit signal even if daily is still up).

Why this design:
  - The daily trend filter is the single biggest source of edge
    in any rule-based trend system. Filters out 50%+ of whipsaws
    that fire counter to the broader trend.
  - Hourly execution improves entry timing vs daily-close entry.
  - Exit on hourly cross-down is asymmetric on purpose: we want
    to give back less on a real reversal than we'd give back
    waiting for the daily to roll.

No-look-ahead: the daily SMA is read from `ctx.history_at('1d')`
which the Backtester only releases on bars whose `ready_time`
(daily_bar.timestamp + 1 day) has passed. Test
`test_backtester_releases_coarser_bars_only_when_ready` pins this.

Modularity contract: pure strategy. Indicators reached by name +
interval through Context. No `app.db.*` / `app.providers.*` imports.
"""
from __future__ import annotations

import logging
import math
from typing import Optional

import pandas as pd
from pydantic import BaseModel, Field

from app.services.sim.context import Context
from app.services.sim.schemas import Action, hold

logger = logging.getLogger(__name__)


class MtfEmaTrendFilteredParams(BaseModel):
    daily_trend_period: int = Field(
        50, ge=10, le=400,
        description=(
            "Period for the daily SMA trend filter. 50 = medium-term "
            "trend (~2 months); 200 = long-term (~1 year). Smaller "
            "periods warm up faster — useful for shorter backtest "
            "windows."
        ),
    )
    fast_period: int = Field(12, ge=2, le=200)
    slow_period: int = Field(26, ge=3, le=200)
    position_size_pct: float = Field(0.95, gt=0.0, le=1.0)

    def validate_periods(self) -> None:
        if self.fast_period >= self.slow_period:
            raise ValueError(
                f"fast_period ({self.fast_period}) must be < slow_period "
                f"({self.slow_period}) — otherwise the cross signal degenerates."
            )


class MtfEmaTrendFilteredStrategy:
    """
    Multi-timeframe strategy: daily SMA trend gate + hourly EMA cross.

    Declares `intervals = ["1d", "1h"]` — Backtester fetches bars at
    both, Context releases the daily ones only when each daily bar's
    window has fully closed.
    """

    name: str = "mtf_ema_trend_filtered"
    version: str = "0.1"
    interval: str = "1h"  # execution
    intervals: list[str] = ["1d", "1h"]  # full set, coarsest-to-finest

    def __init__(
        self,
        params: Optional[MtfEmaTrendFilteredParams] = None,
    ) -> None:
        self.params = params or MtfEmaTrendFilteredParams()
        self.params.validate_periods()
        # No instance interval/intervals override — this strategy is
        # explicitly bound to 1d trend + 1h execution. Variants
        # (e.g. weekly trend + daily entry) would be separate classes.

    def setup(self, ctx: Context) -> None:
        # Stateless across bars; nothing to allocate.
        pass

    def teardown(self, ctx: Context) -> None:
        pass

    def params_dict(self) -> dict:
        """For the agent_runs registry."""
        return self.params.model_dump(mode="json")

    def on_bar(self, ctx: Context) -> Action:
        p = self.params

        # ─── Daily regime gate ─────────────────────────────────────────
        daily_history = ctx.history_at("1d")
        # Need daily_trend_period + 1 bars for SMA to produce a value
        # plus one bar we can use as the "current closed daily."
        if len(daily_history) < p.daily_trend_period + 1:
            return hold()

        daily_df = daily_history.to_dataframe()
        daily_sma_series = ctx.indicator(
            "sma", period=p.daily_trend_period, interval="1d",
        )
        latest_daily_close = float(daily_df["close"].iloc[-1])
        latest_daily_sma = float(daily_sma_series.iloc[-1])

        if _isnan(latest_daily_close) or _isnan(latest_daily_sma):
            return hold()

        trend_up = latest_daily_close > latest_daily_sma

        # ─── Hourly EMA cross detection ────────────────────────────────
        # Need slow_period + 1 hourly bars to detect first cross.
        hourly_history = ctx.history
        if len(hourly_history) < p.slow_period + 1:
            return hold()

        fast_series = ctx.indicator("ema", period=p.fast_period)  # exec interval
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

        # ─── Entry: gated by daily trend ───────────────────────────────
        if cross_up and not has_position and trend_up:
            price = ctx.bar.close
            if price <= 0:
                return hold()
            cash_to_spend = ctx.portfolio.cash * p.position_size_pct
            qty = math.floor(cash_to_spend / price)
            if qty <= 0:
                return hold()
            ctx.log(
                event="signal_buy",
                trend_up=True, daily_close=latest_daily_close, daily_sma=latest_daily_sma,
                hourly_fast=fast_now, hourly_slow=slow_now, qty=qty,
            )
            return Action(
                kind="buy", symbol=symbol, size=float(qty),
                note=(
                    f"mtf: daily trend up ({latest_daily_close:.2f} > "
                    f"{latest_daily_sma:.2f}); hourly cross up "
                    f"({fast_now:.2f} > {slow_now:.2f})"
                ),
            )

        # ─── Exit: NOT gated by trend (respect exit signals always) ───
        if cross_down and has_position:
            qty = position.quantity
            ctx.log(
                event="signal_sell",
                trend_up=trend_up, hourly_fast=fast_now, hourly_slow=slow_now, qty=qty,
            )
            return Action(
                kind="sell", symbol=symbol, size=qty,
                note=(
                    f"mtf: hourly cross down ({fast_now:.2f} < "
                    f"{slow_now:.2f}); daily trend_up={trend_up}"
                ),
            )

        # ─── Skip: cross up but trend is down ──────────────────────────
        if cross_up and not has_position and not trend_up:
            ctx.log(
                event="signal_skipped_trend_filter",
                daily_close=latest_daily_close, daily_sma=latest_daily_sma,
            )

        return hold()


def _last_two(series: pd.Series) -> tuple[float, float]:
    if len(series) < 2:
        return (float("nan"), float("nan"))
    return (float(series.iloc[-1]), float(series.iloc[-2]))


def _isnan(v: float) -> bool:
    return v != v
