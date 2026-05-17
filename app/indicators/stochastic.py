"""Stochastic Oscillator — bounded momentum indicator (%K and %D)."""
from __future__ import annotations

import pandas as pd

from app.indicators.base import Indicator


class StochasticOscillator(Indicator):
    """
    Stochastic Oscillator (Lane, 1950s) — bounded momentum indicator
    measuring close position within the rolling high/low range.

    Raw %K (Lane's "fast K"):

        raw_%K = (Close - LowestLow_n) / (HighestHigh_n - LowestLow_n) * 100

    where LowestLow_n and HighestHigh_n are over the last `period` bars.

    `k_smoothing` smooths raw %K (default 3 — Lane's "slow K"):

        %K = SMA(raw_%K, k_smoothing)

    %D smooths %K further (default 3 — the canonical signal line):

        %D = SMA(%K, d_period)

    Bounded in [0, 100]. Typical reading conventions:
      - Above 80: overbought. Above 80 + crossing down through %D → bearish.
      - Below 20: oversold. Below 20 + crossing up through %D → bullish.
      - %K crosses %D: short-term momentum shift.
      - Bullish/bearish divergence with price: classic reversal signal.

    Requires `high` and `low` Series. Following MACD's pattern,
    `compute()` returns the canonical single-output (smoothed %K).
    Use `compute_full()` for both %K and %D in one pass.

    Params:
      - `period`: lookback for the high/low range (typical: 14, also 9 for faster).
      - `k_smoothing`: smoothing on raw %K (3 = "slow stochastic"; 1 = "fast stochastic").
      - `d_period`: smoothing for %D over %K (typical: 3).
    """

    def __init__(
        self,
        period: int = 14,
        k_smoothing: int = 3,
        d_period: int = 3,
    ) -> None:
        super().__init__()
        if period < 2:
            raise ValueError(f"Stochastic period must be >= 2, got {period}")
        if k_smoothing < 1:
            raise ValueError(f"k_smoothing must be >= 1, got {k_smoothing}")
        if d_period < 1:
            raise ValueError(f"d_period must be >= 1, got {d_period}")
        self.name = f"stochastic_{period}_{k_smoothing}_{d_period}"
        self.period = period
        self.k_smoothing = k_smoothing
        self.d_period = d_period

    def compute(
        self,
        close: pd.Series,
        high: pd.Series | None = None,
        low: pd.Series | None = None,
    ) -> pd.Series:
        """Returns smoothed %K. For both %K and %D in one pass use `compute_full`."""
        if high is None or low is None:
            raise ValueError("Stochastic requires `high` and `low` series.")
        return self._smoothed_k(close, high, low)

    def compute_full(
        self,
        close: pd.Series,
        high: pd.Series | None = None,
        low: pd.Series | None = None,
    ) -> dict[str, pd.Series]:
        """
        Returns dict {'k': smoothed %K, 'd': %D}.
        Use `compute_full()` when the consumer needs both — saves a
        redundant rolling pass over the smoothed %K.
        """
        if high is None or low is None:
            raise ValueError("Stochastic requires `high` and `low` series.")
        k = self._smoothed_k(close, high, low)
        d = k.rolling(window=self.d_period, min_periods=self.d_period).mean()
        return {"k": k, "d": d}

    def _smoothed_k(
        self, close: pd.Series, high: pd.Series, low: pd.Series,
    ) -> pd.Series:
        lowest_low = low.rolling(window=self.period, min_periods=self.period).min()
        highest_high = high.rolling(window=self.period, min_periods=self.period).max()
        range_ = highest_high - lowest_low
        # Guard against zero range (a perfectly flat bar window) → NaN, not divide-by-zero.
        raw_k = (close - lowest_low) / range_.where(range_ > 0) * 100.0
        if self.k_smoothing == 1:
            return raw_k
        return raw_k.rolling(
            window=self.k_smoothing, min_periods=self.k_smoothing,
        ).mean()
