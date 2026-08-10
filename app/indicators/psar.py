"""Parabolic SAR (Wilder) — stop-and-reverse trend level."""
from __future__ import annotations

import numpy as np
import pandas as pd

from app.indicators.base import Indicator

# Bars consumed seeding the recursion before the SAR is meaningful. The
# first two values are pure seed (see `compute`); the accelerator needs a
# handful more to shake off the seed's influence. Returned as NaN so no
# consumer can mistake a seed artifact for a reading.
_WARMUP_BARS = 2


class PSAR(Indicator):
    """
    Parabolic Stop-And-Reverse (Wilder 1978).

    A trailing level that sits *below* price in an uptrend and *above* it in a
    downtrend, converging on price as the trend extends. Price crossing the
    level flips the trend — hence "stop and reverse". Its single output is a
    price, not an oscillator: the tradeable reading is the **side**,
    `close > psar` (bullish) vs `close < psar` (bearish).

    Recursion, for an uptrend (mirror for a downtrend):

        SAR_{t+1} = SAR_t + AF * (EP - SAR_t)

    where `EP` (extreme point) is the highest high since the trend began and
    `AF` (acceleration factor) starts at `af_start`, increments by `af_step`
    *each time a new EP is set*, and caps at `af_max`. A larger `af_max` makes
    the level converge on price faster and flip more often.

    Wilder's two constraints, both implemented:

    1. On a flip, the new SAR is the *prior* trend's extreme point, and AF
       resets to `af_start`.
    2. The SAR may never penetrate the prior two bars' range — in an uptrend
       it is clamped to at most `min(low_{t-1}, low_{t-2})`. Without this the
       level can sit inside the current bar and produce phantom flips.

    Parameters follow the convention used by every charting platform (and by
    the vendor charts this was built for, e.g. `P-Sar[0.028, 0.214]`): two
    numbers, the acceleration and its maximum, where the acceleration doubles
    as the per-EP increment. `af_step` is exposed separately for the rare case
    where they differ.

    Note the recursion is **path-dependent with no closed form** — its value at
    bar `t` depends on every bar back to the seed. It is causal (no look-ahead),
    but two callers who slice the same series differently will not agree on the
    first few dozen bars. Compute it once over the full history rather than
    per-window.
    """

    def __init__(
        self,
        af_start: float = 0.02,
        af_max: float = 0.2,
        af_step: float | None = None,
    ) -> None:
        super().__init__()
        if af_start <= 0:
            raise ValueError(f"PSAR af_start must be > 0, got {af_start}")
        if af_max < af_start:
            raise ValueError(
                f"PSAR af_max ({af_max}) must be >= af_start ({af_start})"
            )
        step = af_start if af_step is None else af_step
        if step <= 0:
            raise ValueError(f"PSAR af_step must be > 0, got {step}")

        self.af_start = af_start
        self.af_max = af_max
        self.af_step = step
        self.name = f"psar_{af_start}_{af_max}"
        self.period = None

    def compute(
        self,
        close: pd.Series,
        high: pd.Series | None = None,
        low: pd.Series | None = None,
    ) -> pd.Series:
        if high is None or low is None:
            raise ValueError("PSAR requires `high` and `low` series in addition to `close`.")

        n = len(close)
        if n <= _WARMUP_BARS:
            return pd.Series(np.nan, index=close.index, dtype="float64")

        h = high.to_numpy(dtype="float64")
        lo = low.to_numpy(dtype="float64")
        c = close.to_numpy(dtype="float64")

        sar = np.empty(n, dtype="float64")

        # Seed. The recursion needs a starting level, a starting trend, and a
        # starting extreme point. Direction comes from the first two closes;
        # the level is the opposing extreme of the first bar — an uptrend's SAR
        # starts at the first low, a downtrend's at the first high.
        #
        # Seeding the level from *close* (as several off-the-shelf
        # implementations do) puts it inside the second bar's range and
        # manufactures a flip on bar 2 in an otherwise clean trend. Wilder's
        # seed cannot, because an extreme of bar 0 is by construction outside
        # the body of bar 1 in the direction the level trails from.
        up = c[1] >= c[0]
        af = self.af_start
        ep_high = max(h[0], h[1])     # extreme point while up
        ep_low = min(lo[0], lo[1])    # extreme point while down
        sar[0] = np.nan
        sar[1] = lo[0] if up else h[0]

        for i in range(_WARMUP_BARS, n):
            reversed_here = False

            if up:
                sar[i] = sar[i - 1] + af * (ep_high - sar[i - 1])
                if lo[i] < sar[i]:
                    # Price broke the trailing level — flip to a downtrend and
                    # restart from the uptrend's extreme high (constraint 1).
                    reversed_here = True
                    sar[i] = ep_high
                    ep_low = lo[i]
                    af = self.af_start
                else:
                    if h[i] > ep_high:
                        ep_high = h[i]
                        af = min(af + self.af_step, self.af_max)
                    # Constraint 2: never above the prior two lows.
                    sar[i] = min(sar[i], lo[i - 1], lo[i - 2])
            else:
                sar[i] = sar[i - 1] - af * (sar[i - 1] - ep_low)
                if h[i] > sar[i]:
                    reversed_here = True
                    sar[i] = ep_low
                    ep_high = h[i]
                    af = self.af_start
                else:
                    if lo[i] < ep_low:
                        ep_low = lo[i]
                        af = min(af + self.af_step, self.af_max)
                    # Constraint 2, mirrored: never below the prior two highs.
                    sar[i] = max(sar[i], h[i - 1], h[i - 2])

            up = up != reversed_here      # XOR — flip only on a reversal bar

        sar[:_WARMUP_BARS] = np.nan
        return pd.Series(sar, index=close.index, dtype="float64")
