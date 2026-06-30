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

    Warmup: `min_periods=period` masks the first `period - 1` values
    as NaN. Underneath, the recursion still seeds from `close.iloc[0]`
    and runs through every bar — the mask only hides the under-formed
    head, it does NOT change the seed.

    Seed continuity (load-bearing for cross-timeframe MAs): because the
    recursion seeds from the first bar it is handed, an EMA computed over
    a truncated window is NOT equal to the same EMA computed over full
    history and then sliced — the seed drifts and decays as
    `(1 - alpha)^n`. Callers that need a window's EMA to match the "true"
    value (e.g. a 200-bar EMA shown on a zoomed-in chart) MUST feed
    continuous history extending well before the window start, not a bare
    slice. The contract for this is pinned in
    `tests/test_ma_timeframe_contract.py`. SMA has no such constraint —
    it is slice-invariant once `period` bars are present.
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
