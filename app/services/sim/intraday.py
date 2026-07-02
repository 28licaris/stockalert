"""
IntradayPath — engine-side hourly-bar provider for path-aware fills.

Mirrors the MarketContext pattern: the Backtester (engine, IO allowed) builds
one IntradayPath per run when `BacktestConfig.hourly_table` is set and attaches
it to every symbol Context as `ctx.intraday`; strategies/filters stay pure and
just READ the day's hourly path (e.g. to order stop-vs-target touches within a
daily bar, or to run working-order entry policies).

No look-ahead contract: callers ask for the path of a bar they are currently
processing (`bars_for(symbol, day)` where `day` is the CURRENT daily bar's
date) — the same information a live trader had by that bar's close.

Loading is lazy per symbol (whole run range in one query, cached): only names
that actually get signals/positions ever load, which keeps a 1,000-symbol
portfolio run cheap.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class HourBar:
    timestamp: datetime  # bar START, tz-aware UTC (09:30-anchored ET session)
    open: float
    high: float
    low: float
    close: float
    volume: float


class IntradayPath:
    """Lazy per-symbol hourly bars from a CH table (see build_ohlcv_hourly.py)."""

    def __init__(self, table: str, start, end) -> None:
        import re
        if not re.fullmatch(r"[A-Za-z0-9_]+", table):
            raise ValueError(f"unsafe hourly_table name {table!r}")
        self._table = table
        self._start = start
        self._end = end
        self._cache: dict[str, dict[date, list[HourBar]]] = {}

    def _load_symbol(self, symbol: str) -> dict[date, list[HourBar]]:
        from datetime import timezone
        from app.db.client import get_client
        by_day: dict[date, list[HourBar]] = {}
        rows = get_client().query(
            f"SELECT timestamp, open, high, low, close, volume FROM {self._table} FINAL "
            "WHERE symbol = {s:String} AND timestamp >= {a:String} AND timestamp <= {b:String} "
            "ORDER BY timestamp",
            parameters={"s": symbol,
                        "a": self._start.strftime("%Y-%m-%d %H:%M:%S"),
                        "b": self._end.strftime("%Y-%m-%d %H:%M:%S")},
        ).result_rows
        for ts, o, h, lo, c, v in rows:
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            # Group by the ET trading day the bar belongs to. Bar starts span
            # 09:30-15:30 ET; UTC date == ET date for those hours, so the UTC
            # date is safe here (no after-hours bars in this table).
            by_day.setdefault(ts.date(), []).append(
                HourBar(ts, float(o), float(h), float(lo), float(c), float(v)))
        return by_day

    def bars_for(self, symbol: str, day: date) -> list[HourBar]:
        """The symbol's hourly path for one trading day (possibly empty)."""
        if symbol not in self._cache:
            self._cache[symbol] = self._load_symbol(symbol)
        return self._cache[symbol].get(day, [])
