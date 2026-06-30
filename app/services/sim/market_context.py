"""
MarketContext — benchmark/market state exposed to strategies & filters.

PURE data holder (pandas only, no IO): the **engine** (Backtester) loads the
benchmark bars and constructs this; the Context exposes it as `ctx.market` so
filters can ask market-relative questions (regime, relative strength) WITHOUT
importing data layers — which keeps strategies/filters past the purity gate.

All lookups are **as-of** a timestamp (most recent benchmark bar at or before
it), so they are no-look-ahead by construction.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd


class MarketContext:
    def __init__(self, benchmark: str, close: pd.Series) -> None:
        self.benchmark = benchmark
        # Ascending, timestamp-indexed close series for the benchmark.
        self._close = close.sort_index() if len(close) else close

    def _upto(self, ts: datetime) -> pd.Series:
        if len(self._close) == 0:
            return self._close
        return self._close[self._close.index <= pd.Timestamp(ts)]

    def value_asof(self, ts: datetime) -> Optional[float]:
        s = self._upto(ts)
        return float(s.iloc[-1]) if len(s) else None

    def above_ma_asof(self, ts: datetime, period: int) -> Optional[bool]:
        """Is the benchmark above its trailing SMA(period) as of `ts`? None if warming up."""
        s = self._upto(ts)
        if len(s) < period:
            return None
        ma = float(s.iloc[-period:].mean())
        return float(s.iloc[-1]) > ma

    def return_over_asof(self, ts: datetime, n: int) -> Optional[float]:
        """Benchmark fractional return over the trailing `n` bars as of `ts`."""
        s = self._upto(ts)
        if len(s) < n + 1:
            return None
        prev = float(s.iloc[-1 - n])
        return (float(s.iloc[-1]) / prev - 1.0) if prev else None
