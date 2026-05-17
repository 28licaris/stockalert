"""Exponential Moving Average (EMA)."""
from __future__ import annotations

import pandas as pd

from app.indicators.base import Indicator


class EMA(Indicator):
    """
    Exponential moving average over `period` closes.

    Uses pandas' `ewm(span=period, adjust=False)` — the standard
    "smoothing factor 2/(period+1)" definition that matches every
    charting platform (TradingView, ThinkOrSwim, Schwab).

    Warmup: the very first value is just `close[0]` (no smoothing
    yet); subsequent values converge. Most TA conventions consider
    EMA "valid" after `period` bars — strategies that care should
    skip the first `period - 1` values.
    """

    def __init__(self, period: int = 20) -> None:
        super().__init__()
        if period < 1:
            raise ValueError(f"EMA period must be >= 1, got {period}")
        self.name = f"ema_{period}"
        self.period = period

    def compute(
        self,
        close: pd.Series,
        high: pd.Series | None = None,
        low: pd.Series | None = None,
    ) -> pd.Series:
        return close.ewm(span=self.period, adjust=False, min_periods=self.period).mean()
