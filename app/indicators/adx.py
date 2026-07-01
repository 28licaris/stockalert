"""Average Directional Index (ADX) — Wilder's trend-strength measure."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.base import Indicator


class ADX(Indicator):
    """
    Average Directional Index (Wilder 1978) — measures trend *strength*
    irrespective of direction. The professional's "is this actually trending,
    or just chopping?" gate: ADX < 20 = no trend (avoid trend-following entries),
    ADX > 25 = trending, ADX > 40 = very strong.

    Construction:
        +DM = High_t - High_{t-1}   (if > Low_{t-1} - Low_t and > 0, else 0)
        -DM = Low_{t-1} - Low_t     (if > High_t - High_{t-1} and > 0, else 0)
        TR  = max(H-L, |H-C_{t-1}|, |L-C_{t-1}|)
        +DI = 100 * Wilder(+DM) / Wilder(TR)
        -DI = 100 * Wilder(-DM) / Wilder(TR)
        DX  = 100 * |+DI - -DI| / (+DI + -DI)
        ADX = Wilder(DX)

    Wilder smoothing = recursive EWMA with α = 1/n (`ewm(alpha=1/n,
    adjust=False)`) — the same convention as `ATR`, matching TradingView / ToS.
    Returns the ADX line; NaN during warmup (needs ~2·period bars to converge).

    `compute()` requires `high` and `low` in addition to `close`.
    """

    def __init__(self, period: int = 14) -> None:
        super().__init__()
        if period < 1:
            raise ValueError(f"ADX period must be >= 1, got {period}")
        self.name = f"adx_{period}"
        self.period = period

    def compute(
        self,
        close: pd.Series,
        high: pd.Series | None = None,
        low: pd.Series | None = None,
    ) -> pd.Series:
        if high is None or low is None:
            raise ValueError("ADX requires `high` and `low` series in addition to `close`.")

        n = self.period
        up_move = high.diff()
        down_move = -low.diff()
        plus_dm = up_move.where((up_move > down_move) & (up_move > 0.0), 0.0)
        minus_dm = down_move.where((down_move > up_move) & (down_move > 0.0), 0.0)

        prev_close = close.shift(1)
        tr = pd.concat([
            (high - low).abs(),
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ], axis=1).max(axis=1)

        def wilder(s: pd.Series) -> pd.Series:
            return s.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()

        atr = wilder(tr)
        plus_di = 100.0 * wilder(plus_dm) / atr
        minus_di = 100.0 * wilder(minus_dm) / atr
        di_sum = (plus_di + minus_di).replace(0.0, np.nan)
        dx = 100.0 * (plus_di - minus_di).abs() / di_sum
        adx = dx.ewm(alpha=1.0 / n, adjust=False, min_periods=n).mean()
        return adx
