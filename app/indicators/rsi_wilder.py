"""
True Wilder RSI — recursive smoothing with alpha = 1/period.

Distinct from `rsi.py`, whose EMA uses span=period (alpha = 2/(period+1))
— that is STANDARD EMA smoothing, not Wilder's. Wilder's original
smoothing is AvgGain_t = AvgGain_{t-1} + (Gain_t - AvgGain_{t-1})/n,
i.e. a recursive EWMA with alpha = 1/n (equivalently span = 2n-1).
For small periods the difference is material: RSI(4) has alpha 0.25
under Wilder vs 0.4 under span-4 EMA — different signal timing.

Registered separately ("rsi_wilder") rather than changing "rsi", because
live alerts/filters/UI already depend on the existing behavior (see
docs/ISSUES.md). This implementation is the byte-parity twin of the
research screen's math (scripts/mcpt_insample._wilder_rsi_wide and the
ranker dataset's _wilder_rsi) — required so backtests of screen-validated
rules test the SAME rule (EXP-40).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.base import Indicator


class RSIWilder(Indicator):
    """RSI with Wilder's smoothing (alpha = 1/period, NaN until warmup)."""

    def __init__(self, period: int = 14):
        super().__init__()
        self.period = period
        self.name = "rsi_wilder"

    def compute(self, close: pd.Series, high=None, low=None) -> pd.Series:
        d = close.diff()
        up = d.clip(lower=0).ewm(
            alpha=1 / self.period, adjust=False, min_periods=self.period
        ).mean()
        dn = (-d.clip(upper=0)).ewm(
            alpha=1 / self.period, adjust=False, min_periods=self.period
        ).mean()
        rs = up / dn.replace(0, np.nan)
        return 100 - 100 / (1 + rs)
