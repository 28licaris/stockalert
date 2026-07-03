"""
Context object passed to `Strategy.on_bar`. Strategy-facing read-only
view of the world: current bar, recent history (per interval),
portfolio snapshot, indicator API, structured logging.

The backtester owns one `Context` per run and mutates it via the
`advance` (execution interval) and `advance_coarser` (other intervals)
methods. Strategies see the same object across calls but treat it as
read-only.

Multi-timeframe support (TA-4):

  - Single-timeframe strategies work unchanged: `ctx.history` and
    `ctx.indicator(name, **params)` operate on the only interval.
  - Multi-timeframe strategies declare `intervals = ['1d', '1h', '5m']`
    (coarsest-to-finest). Backtester iterates the finest;
    coarser-interval bars become visible to the strategy via
    `ctx.history_at(interval)` and `ctx.indicator(..., interval=)`.
  - No look-ahead: coarser bars are exposed only when their ready
    time (timestamp + duration) is <= current execution timestamp.
    The backtester enforces this via `advance_coarser`.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
from typing import Any, Optional

import numpy as np
import pandas as pd

from app.indicators.registry import get_indicator
from app.services.sim.intervals import execution_interval, validate_intervals_order
from app.services.sim.schemas import Bar, BacktestConfig, PortfolioSnapshot


class BarHistory:
    """
    Rolling window of the most recent `maxlen` bars at one interval.

    Backed by `collections.deque(maxlen=...)` for O(1) appends with
    automatic eviction. Materialized as pandas DataFrame on demand
    (cached and invalidated on each `append`) so indicators can use
    standard pandas APIs.
    """

    _COLS = ("open", "high", "low", "close", "volume")

    def __init__(self, maxlen: int) -> None:
        if maxlen < 1:
            raise ValueError(f"BarHistory maxlen must be >= 1, got {maxlen}")
        self._bars: deque[Bar] = deque(maxlen=maxlen)
        # Parallel per-column buffers (floats + int64 epoch-ns timestamps) so
        # DataFrames/Series build from arrays via pandas' fast C paths instead
        # of a list of dicts — this runs once per bar per symbol and dominated
        # engine profiles (EXP-40 perf pass). Same values, same index, same
        # column order as the original list-of-dicts construction; equivalence
        # enforced by scripts/dev/engine_fingerprint.py.
        self._ts_ns: deque = deque(maxlen=maxlen)
        self._tz = None  # tzinfo of the appended bars (assumed consistent per run)
        self._cols: dict[str, deque] = {c: deque(maxlen=maxlen) for c in self._COLS}
        self._df_cache: Optional[pd.DataFrame] = None
        self._index_cache: Optional[pd.DatetimeIndex] = None
        self._series_cache: dict[str, pd.Series] = {}

    def append(self, bar: Bar) -> None:
        self._bars.append(bar)
        self._ts_ns.append(pd.Timestamp(bar.timestamp).value)
        self._tz = bar.timestamp.tzinfo
        for c, dq in self._cols.items():
            dq.append(getattr(bar, c))
        self._df_cache = None  # invalidate
        self._index_cache = None
        self._series_cache.clear()

    def __len__(self) -> int:
        return len(self._bars)

    @property
    def maxlen(self) -> int:
        return self._bars.maxlen or 0

    def _index(self) -> pd.DatetimeIndex:
        if self._index_cache is None:
            ns = np.fromiter(self._ts_ns, dtype="int64", count=len(self._ts_ns))
            idx = pd.DatetimeIndex(ns.view("datetime64[ns]"), name="timestamp")
            if self._tz is not None:
                # Timestamp.value is epoch-ns UTC; localize then present in the
                # bars' own tz (identical to indexing the aware datetimes directly).
                idx = idx.tz_localize("UTC").tz_convert(self._tz)
            self._index_cache = idx
        return self._index_cache

    def _column(self, col: str) -> np.ndarray:
        dq = self._cols[col]
        return np.fromiter(dq, dtype="float64", count=len(dq))

    def series(self, col: str) -> pd.Series:
        """One OHLCV column as a timestamp-indexed Series (cached per bar)."""
        cached = self._series_cache.get(col)
        if cached is None:
            cached = pd.Series(self._column(col), index=self._index(), name=col)
            self._series_cache[col] = cached
        return cached

    def to_dataframe(self) -> pd.DataFrame:
        """OHLCV DataFrame indexed by timestamp. Cached until the next `append`."""
        if self._df_cache is not None:
            return self._df_cache

        if not self._bars:
            df = pd.DataFrame()
        else:
            df = pd.DataFrame(
                {c: self._column(c) for c in self._COLS}, index=self._index()
            )
        self._df_cache = df
        return df

    def last(self, n: int = 1) -> list[Bar]:
        """Return the last `n` bars (newest at index -1)."""
        if n <= 0:
            return []
        return list(self._bars)[-n:]


class Context:
    """
    Per-bar view passed to `Strategy.on_bar`. Owns one `BarHistory`
    per interval and the cross-interval indicator cache.

    Lifecycle (single-TF):
      - `__init__(config)` once per run. Defaults to single interval
        `config.interval`.
      - `advance(bar, portfolio)` before each `on_bar` call.

    Lifecycle (multi-TF):
      - `__init__(config, intervals=['1d', '1h', '5m'])` once per run.
      - Per iteration (executed at `intervals[-1]`, the finest):
          1. For each coarser interval, the backtester calls
             `advance_coarser(interval, bar)` for any newly-ready bar.
          2. `advance(execution_bar, portfolio)` once.
          3. Strategy.on_bar(ctx) reads from any interval.

    Indicator cache invalidates at every `advance()` call (execution
    interval) — every fresh execution bar means at least one
    BarHistory has new data, so all indicator queries recompute.
    """

    def __init__(
        self,
        config: BacktestConfig,
        *,
        intervals: Optional[list[str]] = None,
    ) -> None:
        self.config = config
        # Optional benchmark/market state, set by the engine (Backtester) when a
        # benchmark is configured. Filters read it via `ctx.market`; None when
        # no benchmark is loaded. Duck-typed (a MarketContext) to avoid pulling
        # data-layer imports into this purity-gated module.
        self.market = None
        # Back-compat: if intervals not given, use [config.interval].
        # Otherwise honor what the strategy declared.
        self._intervals = list(intervals) if intervals else [config.interval]
        validate_intervals_order(self._intervals)
        self._exec_interval = execution_interval(self._intervals)
        # One BarHistory per interval, all sized to history_window.
        # (Coarser intervals need fewer bars to cover the same time
        # span as the finest, but a uniform maxlen keeps this simple
        # and is rarely the bottleneck.)
        self._histories: dict[str, BarHistory] = {
            iv: BarHistory(maxlen=config.history_window) for iv in self._intervals
        }
        self._bar: Optional[Bar] = None
        self._portfolio: Optional[PortfolioSnapshot] = None
        # Cross-interval indicator cache. Key = (interval, name_lower,
        # sorted-params-tuple). Invalidated wholesale on every advance().
        self._indicator_cache: dict[tuple, pd.Series] = {}
        self._log_entries: list[dict[str, Any]] = []

    # ─────────────────────────────────────────────────────────────────
    # Public read-only state
    # ─────────────────────────────────────────────────────────────────

    @property
    def intervals(self) -> list[str]:
        """Configured intervals, coarsest-to-finest."""
        return list(self._intervals)

    @property
    def execution_interval(self) -> str:
        """Finest interval — the one the backtester iterates on."""
        return self._exec_interval

    @property
    def history(self) -> BarHistory:
        """
        BarHistory at the execution interval. Single-TF strategies
        use this without ever thinking about intervals.
        """
        return self._histories[self._exec_interval]

    def history_at(self, interval: str) -> BarHistory:
        """
        BarHistory at a specific interval. Multi-TF strategies use
        this to peek at coarser timeframes:
            daily_history = ctx.history_at('1d')
        """
        try:
            return self._histories[interval]
        except KeyError as exc:
            raise ValueError(
                f"interval {interval!r} not declared by this strategy/run; "
                f"available: {self._intervals}"
            ) from exc

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
        """Timestamp of the current EXECUTION bar."""
        return self.bar.timestamp

    # ─────────────────────────────────────────────────────────────────
    # Harness-only mutation
    # ─────────────────────────────────────────────────────────────────

    def advance(self, bar: Bar, portfolio: PortfolioSnapshot) -> None:
        """
        Advance the execution interval and reset the indicator cache.
        Called once per iteration step by the backtester.
        """
        self._bar = bar
        self._portfolio = portfolio
        self._histories[self._exec_interval].append(bar)
        self._indicator_cache.clear()

    def advance_coarser(self, interval: str, bar: Bar) -> None:
        """
        Advance a coarser interval's history. Called by the backtester
        BEFORE `advance()` when a coarser bar's ready_time has passed.

        Does NOT clear the indicator cache — that happens on the
        next `advance()` call. Multiple coarser advances may happen
        in sequence (e.g. catching up several days' worth of data).
        """
        if interval == self._exec_interval:
            raise ValueError(
                f"advance_coarser called with the execution interval {interval!r}; "
                "use advance() for execution-interval bars."
            )
        if interval not in self._histories:
            raise ValueError(
                f"interval {interval!r} not declared; available: {self._intervals}"
            )
        self._histories[interval].append(bar)

    # ─────────────────────────────────────────────────────────────────
    # Strategy-facing computation
    # ─────────────────────────────────────────────────────────────────

    def indicator(
        self,
        name: str,
        *,
        interval: Optional[str] = None,
        **params: Any,
    ) -> pd.Series:
        """
        Lazy-compute + per-bar-cache an indicator at a specific interval.

        Strategies call:
            ctx.indicator("sma", period=20)              # execution interval
            ctx.indicator("rsi", period=14, interval="1d")  # daily, multi-TF
            ctx.indicator("ema", period=12, interval="5m")  # 5-min, multi-TF

        Returns a pandas Series aligned to that interval's
        `history.to_dataframe()` index. Empty if history is empty.

        Cache scope: ONE bar. Each `advance()` clears it.

        Raises `ValueError` for unknown indicator names OR for an
        interval not declared in `self.intervals`.
        """
        target_interval = interval or self._exec_interval
        if target_interval not in self._histories:
            raise ValueError(
                f"interval {target_interval!r} not declared; "
                f"available: {self._intervals}"
            )

        key: tuple = (
            target_interval,
            name.lower(),
            tuple(sorted(params.items())),
        )
        cached = self._indicator_cache.get(key)
        if cached is not None:
            return cached

        hist = self._histories[target_interval]
        if len(hist) == 0:
            result = pd.Series(dtype="float64", name=name)
        else:
            ind = get_indicator(name, **params)
            # Per-column cached Series — avoids materializing the full OHLCV
            # DataFrame for every indicator call (EXP-40 perf pass).
            result = ind.compute(hist.series("close"), hist.series("high"), hist.series("low"))

        self._indicator_cache[key] = result
        return result

    def log(self, **fields: Any) -> None:
        """Record a structured per-bar log entry."""
        entry = {"timestamp": self.clock, **fields}
        self._log_entries.append(entry)

    @property
    def log_entries(self) -> list[dict[str, Any]]:
        """Read-only view of accumulated log entries."""
        return list(self._log_entries)
