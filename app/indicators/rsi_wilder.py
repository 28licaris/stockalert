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
        # numpy pre/post-processing; the ewm recursion stays in pandas so the
        # smoothing is bit-identical to the reference formulation
        # (d.clip -> ewm -> replace(0, nan)). This runs once per bar per
        # symbol in backtests — pandas clip/replace overhead dominated engine
        # profiles. Equivalence enforced by scripts/dev/engine_fingerprint.py
        # and tests/test_rsi_wilder_parity.py.
        c = close.to_numpy(dtype=float)
        d = np.empty_like(c)
        d[0] = np.nan
        d[1:] = c[1:] - c[:-1]
        nan_mask = np.isnan(d)
        up_arr = np.where(d > 0, d, 0.0)
        dn_arr = np.where(d < 0, -d, 0.0)
        up_arr[nan_mask] = np.nan  # clip() preserves NaN; where() would zero it
        dn_arr[nan_mask] = np.nan
        kw = dict(alpha=1 / self.period, adjust=False, min_periods=self.period)
        up = pd.Series(up_arr, index=close.index).ewm(**kw).mean().to_numpy()
        dn = pd.Series(dn_arr, index=close.index).ewm(**kw).mean().to_numpy(copy=True)
        dn[dn == 0] = np.nan  # replace(0, nan)
        rsi = 100 - 100 / (1 + up / dn)
        return pd.Series(rsi, index=close.index)
