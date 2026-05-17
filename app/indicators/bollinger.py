"""Bollinger Bands — volatility envelope around an SMA."""
from __future__ import annotations

import pandas as pd

from app.indicators.base import Indicator


class BollingerBands(Indicator):
    """
    Bollinger Bands (Bollinger 1980s) — a volatility envelope of
    `std_multiplier` rolling-stdev around an SMA midline.

    Components:

        middle = SMA(close, period)
        std    = rolling stdev(close, period)
        upper  = middle + std_multiplier * std
        lower  = middle - std_multiplier * std
        bandwidth = (upper - lower) / middle              (relative band width)
        percent_b = (close - lower) / (upper - lower)     (where price sits in the band)

    Typical params: `period=20, std_multiplier=2.0` (Bollinger's
    original). For longer-term: 50/2. For shorter / scalp: 10/1.5.

    Mean-revert reading: price at lower band → oversold; at upper →
    overbought. Caveats: trending markets ride the band, so mean-
    revert signals fail in strong trends — pair with a trend filter.

    Breakout reading: band squeeze (low bandwidth) often precedes
    expansion; range-bound markets breaking through a band sometimes
    signal regime change.

    Following MACD's pattern: `compute()` returns the canonical
    single-output (the middle band — the SMA midline). Use
    `compute_full()` to get all components in one pass for chart
    overlay / strategy use.
    """

    def __init__(self, period: int = 20, std_multiplier: float = 2.0) -> None:
        super().__init__()
        if period < 2:
            raise ValueError(f"BollingerBands period must be >= 2, got {period}")
        if std_multiplier <= 0:
            raise ValueError(f"std_multiplier must be > 0, got {std_multiplier}")
        self.name = f"bollinger_{period}_{std_multiplier}"
        self.period = period
        self.std_multiplier = float(std_multiplier)

    def compute(
        self,
        close: pd.Series,
        high: pd.Series | None = None,
        low: pd.Series | None = None,
    ) -> pd.Series:
        """Returns the middle band (the SMA). For all components use `compute_full`."""
        return close.rolling(window=self.period, min_periods=self.period).mean()

    def compute_full(
        self,
        close: pd.Series,
        high: pd.Series | None = None,
        low: pd.Series | None = None,
    ) -> dict[str, pd.Series]:
        """
        Compute all Bollinger components in one rolling pass. Returns
        a dict so the `IndicatorReader` can decompose into multiple
        named `IndicatorSeries` entries in the response.

        Keys:
          - `upper`     — middle + k * std
          - `middle`    — SMA(period)
          - `lower`     — middle - k * std
          - `bandwidth` — (upper - lower) / middle
          - `percent_b` — (close - lower) / (upper - lower); 0 at lower band, 1 at upper
        """
        rolling = close.rolling(window=self.period, min_periods=self.period)
        middle = rolling.mean()
        # ddof=0 matches what most charting platforms use (population stdev).
        # Bollinger himself used ddof=0 in the original work.
        std = rolling.std(ddof=0)
        offset = std * self.std_multiplier
        upper = middle + offset
        lower = middle - offset
        bandwidth = (upper - lower) / middle
        percent_b = (close - lower) / (upper - lower)
        return {
            "upper": upper,
            "middle": middle,
            "lower": lower,
            "bandwidth": bandwidth,
            "percent_b": percent_b,
        }
