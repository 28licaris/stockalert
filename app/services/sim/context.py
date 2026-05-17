"""
Context object passed to `Strategy.on_bar`. Strategy-facing read-only
view of the world: current bar, recent history, portfolio snapshot,
indicator API, structured logging.

The backtester owns one `Context` per run and mutates it via the
`advance(bar, portfolio)` method on each iteration. Strategies see
the same object across calls but treat it as read-only.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any, Optional

import pandas as pd

from app.indicators.registry import get_indicator
from app.services.sim.schemas import Bar, BacktestConfig, PortfolioSnapshot


class BarHistory:
    """
    Rolling window of the most recent `maxlen` bars.

    Backed by `collections.deque(maxlen=...)` for O(1) appends with
    automatic eviction. Materialized as pandas DataFrame on demand
    (cached and invalidated on each `append`) so indicators can use
    standard pandas APIs.

    Sized to the slowest indicator a strategy uses — for an SMA(200)
    crossover, history_window=200 is the minimum; ~250 gives some
    breathing room.
    """

    def __init__(self, maxlen: int) -> None:
        if maxlen < 1:
            raise ValueError(f"BarHistory maxlen must be >= 1, got {maxlen}")
        self._bars: deque[Bar] = deque(maxlen=maxlen)
        self._df_cache: Optional[pd.DataFrame] = None

    def append(self, bar: Bar) -> None:
        self._bars.append(bar)
        self._df_cache = None  # invalidate

    def __len__(self) -> int:
        return len(self._bars)

    @property
    def maxlen(self) -> int:
        return self._bars.maxlen or 0

    def to_dataframe(self) -> pd.DataFrame:
        """
        Materialize bars as DataFrame indexed by timestamp.

        Cached until the next `append`. Columns: open, high, low,
        close, volume — the standard OHLCV shape indicators expect.
        Strategies can also use `df['close']` for indicator input.
        """
        if self._df_cache is not None:
            return self._df_cache

        rows = [
            {
                "timestamp": b.timestamp,
                "open": b.open, "high": b.high, "low": b.low,
                "close": b.close, "volume": b.volume,
            }
            for b in self._bars
        ]
        df = pd.DataFrame(rows)
        if not df.empty:
            df = df.set_index("timestamp")
        self._df_cache = df
        return df

    def last(self, n: int = 1) -> list[Bar]:
        """Return the last `n` bars (newest at index -1)."""
        if n <= 0:
            return []
        return list(self._bars)[-n:]


class Context:
    """
    Per-bar view passed to `Strategy.on_bar`. Owns the indicator cache.

    Lifecycle:
      - `__init__` once per run.
      - `advance(bar, portfolio)` before each `on_bar` call.
      - Indicator cache keyed on `(name, frozenset(params))` —
        same call within a bar returns the same series; different
        bars recompute (history changed).

    Strategies should treat this as read-only. Mutation belongs to
    the harness.
    """

    def __init__(self, config: BacktestConfig) -> None:
        self.config = config
        self.history = BarHistory(maxlen=config.history_window)
        self._bar: Optional[Bar] = None
        self._portfolio: Optional[PortfolioSnapshot] = None
        # Indicator cache invalidated on each advance() — indicators
        # are functions of history, and history changes each bar.
        self._indicator_cache: dict[tuple, pd.Series] = {}
        # Structured per-bar log entries the strategy emits via `log()`
        self._log_entries: list[dict[str, Any]] = []

    @property
    def bar(self) -> Bar:
        if self._bar is None:
            raise RuntimeError("Context.bar accessed before first advance()")
        return self._bar

    @property
    def portfolio(self) -> PortfolioSnapshot:
        if self._portfolio is None:
            raise RuntimeError("Context.portfolio accessed before first advance()")
        return self._portfolio

    @property
    def clock(self) -> datetime:
        return self.bar.timestamp

    def advance(self, bar: Bar, portfolio: PortfolioSnapshot) -> None:
        """Mutate the context for a new bar (harness-owned)."""
        self._bar = bar
        self._portfolio = portfolio
        self.history.append(bar)
        self._indicator_cache.clear()

    def indicator(self, name: str, **params: Any) -> pd.Series:
        """
        Lazy-compute + per-bar-cache an indicator over the current history.

        Strategies call: `ctx.indicator("sma", period=20)`.
        Returns a pandas Series aligned to `ctx.history.to_dataframe()`'s index.

        Cache scope: ONE bar. Each `advance()` clears it so the
        indicator gets recomputed against the updated history on the
        next call. (Incremental indicators are an optimization for
        later — this gets us correct semantics first.)

        Raises `ValueError` for unknown indicator names.
        """
        key: tuple = (name.lower(), tuple(sorted(params.items())))
        cached = self._indicator_cache.get(key)
        if cached is not None:
            return cached

        df = self.history.to_dataframe()
        if df.empty:
            # Return an empty series with the right name; the strategy
            # will see len() == 0 and skip.
            result = pd.Series(dtype="float64", name=name)
        else:
            ind = get_indicator(name, **params)
            close = df["close"]
            high = df["high"] if "high" in df.columns else None
            low = df["low"] if "low" in df.columns else None
            result = ind.compute(close, high, low)

        self._indicator_cache[key] = result
        return result

    def log(self, **fields: Any) -> None:
        """
        Record a structured per-bar log entry. Captured into the
        RunResult so strategies can leave breadcrumbs without coupling
        to a logger.
        """
        entry = {"timestamp": self.clock, **fields}
        self._log_entries.append(entry)

    @property
    def log_entries(self) -> list[dict[str, Any]]:
        """Read-only view of accumulated log entries."""
        return list(self._log_entries)
