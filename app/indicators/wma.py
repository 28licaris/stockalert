"""Weighted Moving Average (WMA)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.base import Indicator


class WMA(Indicator):
    """
    Weighted moving average — linear weights `[1, 2, ..., period]`.

    More responsive than SMA (recent prices weighted higher) but
    smoother than EMA (no infinite tail). Fills the middle ground in
    the MA family.

    Formula (period n):

        WMA_t = sum_{i=1..n}(i * Close_{t-n+i}) / sum_{i=1..n}(i)
              = sum_{i=1..n}(i * Close_{t-n+i}) / (n * (n+1) / 2)

    Warmup: first `period - 1` values are NaN.
    """

    def __init__(self, period: int = 20) -> None:
        super().__init__()
        if period < 1:
            raise ValueError(f"WMA period must be >= 1, got {period}")
        self.name = f"wma_{period}"
        self.period = period

    def compute(
        self,
        close: pd.Series,
        high: pd.Series | None = None,
        low: pd.Series | None = None,
    ) -> pd.Series:
        weights = np.arange(1, self.period + 1, dtype=float)
        weight_sum = weights.sum()  # = n*(n+1)/2

        # Vectorized via rolling.apply. raw=True passes ndarray (faster
        # than passing a Series each call).
        return close.rolling(window=self.period, min_periods=self.period).apply(
            lambda window: float(np.dot(window, weights) / weight_sum),
            raw=True,
        )
