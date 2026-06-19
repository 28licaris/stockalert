"""Multi-fractal **causal** pivot detector — the foundation of the Elliott
Wave engine (`app/signals/elliott/`).

A *pivot* is a local price extreme: a high that exceeds the `k` bars on each
side, or a low that undercuts them. The single most important property here is
**causality**: a pivot at bar `i` cannot be *confirmed* until `i + k` bars have
printed (you need the right-hand window to know it was an extreme). Every
`Pivot` therefore carries `confirmed_at_index = index + k`, and downstream
consumers must never use a pivot whose confirmation is in the future of the bar
they are labeling. This is what makes wave counts free of look-ahead — see
`docs/elliott_wave_ew1_ew2_spec.md` (D2) and the doctrine skill.

`PivotDetector` is registry-compatible (`Context.indicator("pivots", period=8)`)
via `compute()`, which returns a `+1/-1/0` Series. Elliott-side consumers use
`detect()` / `detect_multidegree()`, which return `Pivot` objects.

This module is new and deliberately does **not** touch `signals/divergence.py`
— that module keeps its close-based helpers unchanged (spec D1).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

import numpy as np
import pandas as pd
from pydantic import BaseModel, ConfigDict

from app.indicators.base import Indicator

# Fibonacci fractal half-windows → wave "degrees". A pivot found with a larger
# `k` is a more significant swing (a higher-degree turn). `degree` is the index
# into this tuple — a bare integer, NOT a textbook name (spec D4).
DEFAULT_KS: tuple[int, ...] = (3, 5, 8, 13, 21)


class Pivot(BaseModel):
    """One confirmed price extreme. Frozen so it is hashable and safe to share
    across candidate labelings without aliasing surprises."""

    model_config = ConfigDict(frozen=True)

    index: int                       # positional bar index of the extreme
    timestamp: datetime              # bar label of the extreme
    price: float                     # the extreme price (high for 'high', low for 'low')
    kind: Literal["high", "low"]
    k: int                           # fractal half-window that detected it
    degree: int = 0                  # index of k within the degree set (D4)
    confirmed_at_index: int          # = index + k ; first bar this pivot is "known" (D2)


class PivotDetector(Indicator):
    """Causal fractal pivot detector.

    Parameters
    ----------
    period : int
        Fractal half-window `k`. A bar is a pivot iff it is the strict extreme
        of `[i-k, i+k]`.
    source : {"hl", "close"}
        "hl" (default, spec D1) detects highs on the `high` series and lows on
        the `low` series — true wick extremes, which is what Elliott measures.
        "close" detects both on `close` (the legacy divergence behaviour).
    strict : bool
        If True (default) the extreme must strictly exceed every neighbour; if
        False, ties are allowed (the bar need only equal the window extreme).
    """

    def __init__(self, period: int = 5, source: Literal["hl", "close"] = "hl",
                 strict: bool = True) -> None:
        super().__init__()
        if period < 1:
            raise ValueError(f"PivotDetector period must be >= 1, got {period}")
        if source not in ("hl", "close"):
            raise ValueError(f"PivotDetector source must be 'hl' or 'close', got {source!r}")
        self.name = f"pivots_{period}"
        self.period = period
        self.source = source
        self.strict = strict

    # -- registry surface ---------------------------------------------------
    def compute(self, close: pd.Series, high: Optional[pd.Series] = None,
                low: Optional[pd.Series] = None) -> pd.Series:
        """+1 at pivot highs, -1 at pivot lows, 0 elsewhere; indexed like input."""
        pivots = self.detect(close, high, low)
        out = pd.Series(0, index=close.index, dtype="int64")
        for p in pivots:
            out.iloc[p.index] = 1 if p.kind == "high" else -1
        return out

    # -- causal surface -----------------------------------------------------
    def detect(self, close: pd.Series, high: Optional[pd.Series] = None,
               low: Optional[pd.Series] = None, *, degree: int = 0) -> list[Pivot]:
        """Return confirmed `Pivot`s, ordered by bar index."""
        k = self.period
        if self.source == "hl":
            if high is None or low is None:
                raise ValueError("PivotDetector(source='hl') requires `high` and `low` series.")
            hi_src, lo_src = high.to_numpy(dtype=float), low.to_numpy(dtype=float)
        else:
            arr = close.to_numpy(dtype=float)
            hi_src = lo_src = arr

        idx = close.index
        n = len(close)
        out: list[Pivot] = []
        for i in range(k, n - k):
            left_h, right_h = hi_src[i - k:i], hi_src[i + 1:i + k + 1]
            c_h = hi_src[i]
            is_high = (c_h >= left_h.max() and c_h >= right_h.max()
                       if not self.strict else
                       c_h > left_h.max() and c_h > right_h.max())
            if is_high:
                out.append(Pivot(index=i, timestamp=_as_dt(idx[i]), price=float(c_h),
                                 kind="high", k=k, degree=degree, confirmed_at_index=i + k))
                continue

            left_l, right_l = lo_src[i - k:i], lo_src[i + 1:i + k + 1]
            c_l = lo_src[i]
            is_low = (c_l <= left_l.min() and c_l <= right_l.min()
                      if not self.strict else
                      c_l < left_l.min() and c_l < right_l.min())
            if is_low:
                out.append(Pivot(index=i, timestamp=_as_dt(idx[i]), price=float(c_l),
                                 kind="low", k=k, degree=degree, confirmed_at_index=i + k))
        return out


def detect_multidegree(close: pd.Series, high: pd.Series, low: pd.Series,
                       ks: tuple[int, ...] = DEFAULT_KS,
                       source: Literal["hl", "close"] = "hl",
                       strict: bool = True) -> list[Pivot]:
    """Run `PivotDetector` once per fractal `k`, tagging each pivot's `degree`
    = `ks.index(k)`. Concatenated and sorted by `(index, degree)`. The engine
    consumes the per-degree streams; no cross-degree subset filtering here."""
    out: list[Pivot] = []
    for degree, k in enumerate(ks):
        det = PivotDetector(period=k, source=source, strict=strict)
        out.extend(det.detect(close, high, low, degree=degree))
    out.sort(key=lambda p: (p.index, p.degree))
    return out


def _as_dt(label) -> datetime:
    """Coerce a pandas index label to a python datetime (Timestamp → datetime;
    ints/other → epoch-based placeholder so non-datetime indices still work)."""
    if isinstance(label, pd.Timestamp):
        return label.to_pydatetime()
    if isinstance(label, datetime):
        return label
    # Non-datetime index (e.g. RangeIndex in tests): synthesise a stable stamp.
    return datetime.fromtimestamp(0) + pd.to_timedelta(int(label), unit="D").to_pytimedelta()
