"""Simple Moving Average (SMA)."""
from __future__ import annotations

import pandas as pd

from app.indicators.base import Indicator


class SMA(Indicator):
    """
    Simple moving average over `period` closes.

    Warmup: the first `period - 1` values are NaN. Strategies should
    filter on `notna()` before comparing crossovers.
    """

    def __init__(self, period: int = 20) -> None:
        super().__init__()
        if period < 1:
            raise ValueError(f"SMA period must be >= 1, got {period}")
        self.name = f"sma_{period}"
        self.period = period

    def compute(
        self,
        close: pd.Series,
        high: pd.Series | None = None,
        low: pd.Series | None = None,
    ) -> pd.Series:
        return close.rolling(window=self.period, min_periods=self.period).mean()
