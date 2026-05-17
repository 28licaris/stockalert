"""Average True Range (ATR) — Wilder's method."""
from __future__ import annotations

import pandas as pd

from app.indicators.base import Indicator


class ATR(Indicator):
    """
    Average True Range — the bedrock volatility measure used for
    stop-loss placement and position sizing in most serious trading
    strategies.

    True Range at bar t (Wilder 1978):

        TR_t = max(
            High_t - Low_t,
            abs(High_t - Close_{t-1}),
            abs(Low_t  - Close_{t-1}),
        )

    Average True Range (Wilder's exponential smoothing with α = 1/n):

        ATR_t = ATR_{t-1} + (TR_t - ATR_{t-1}) / n
              = (n - 1)/n * ATR_{t-1} + 1/n * TR_t

    Pandas equivalent: `ewm(alpha=1/n, adjust=False)`. The
    `adjust=False` is critical — it gives Wilder's recursive form,
    not the unbiased weighted average. Every charting platform
    (TradingView, ToS, Schwab) uses Wilder's form.

    `compute()` requires `high` and `low` Series in addition to
    `close`. Returns NaN for the first `period` bars (need at least
    one prev_close to compute the first TR; Wilder's smoother needs
    `period` TRs to converge meaningfully).

    Typical periods: 14 (Wilder's original), 10 (faster).

    Usage: stop placement → `stop = entry - 2 * ATR`. Position sizing
    → `qty = (account_risk_pct * equity) / (k * ATR)` where k is the
    stop multiple.
    """

    def __init__(self, period: int = 14) -> None:
        super().__init__()
        if period < 1:
            raise ValueError(f"ATR period must be >= 1, got {period}")
        self.name = f"atr_{period}"
        self.period = period

    def compute(
        self,
        close: pd.Series,
        high: pd.Series | None = None,
        low: pd.Series | None = None,
    ) -> pd.Series:
        if high is None or low is None:
            raise ValueError("ATR requires `high` and `low` series in addition to `close`.")

        prev_close = close.shift(1)
        tr = pd.concat([
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)

        # Wilder's smoothing — recursive EWMA with α = 1/n.
        return tr.ewm(alpha=1.0 / self.period, adjust=False, min_periods=self.period).mean()
