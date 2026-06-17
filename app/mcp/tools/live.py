"""
MCP tools backed by `BarReader` — ClickHouse live tier reads.

Use these for seconds-fresh data: today's bars, recent windows, "where
is each symbol right now." For history > T+1 day or ML training, prefer
`tools/lake.py` (CH-independent) instead.

Same Pydantic shapes as `/api/bars` HTTP route.
"""
from __future__ import annotations

import logging
from datetime import datetime
from functools import lru_cache
from typing import Optional

from app.mcp.middleware import tool_call
from app.mcp.server import mcp
from app.services.readers.bar_reader import BarReader
from app.services.readers.bars_gateway import BarSource, get_chart_bars
from app.services.readers.schemas import (
    LatestBarsResponse,
    LiveBar,
    LiveBarsResponse,
)

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _reader() -> BarReader:
    """Memoized reader instance. Stateless under the hood."""
    return BarReader.from_settings()


@mcp.tool()
def get_recent_bars(symbol: str, limit: int = 200) -> LiveBarsResponse:
    """Most recent 1-minute bars for a symbol from ClickHouse, ascending.

    USE WHEN: an agent needs the latest N intraday bars for live
    analysis — current indicator values, "what just happened" context,
    last-bar checks. Sub-second.

    Args:
        symbol: Ticker symbol, e.g. 'AAPL'.
        limit: How many bars to return. Default 200 (~3.3 hours of
            regular-session minutes). Max ~10000.

    Returns:
        LiveBarsResponse with bars sorted oldest-first (so indicator
        code can compute incremental values left-to-right). `interval`
        is always '1m'.

    Cost: 10-50ms typical. Reads from `ohlcv_1m` (ReplacingMergeTree).
    """
    with tool_call("get_recent_bars", symbol=symbol, limit=limit):
        bars = _reader().get_recent_bars(symbol, limit=limit)
        return LiveBarsResponse(
            symbol=symbol, interval="1m", bars=bars, count=len(bars),
        )


@mcp.tool()
def get_bars_in_range(
    symbol: str,
    start: datetime,
    end: datetime,
    interval: str = "1m",
    limit: int = 100_000,
    source_table: Optional[str] = None,
) -> LiveBarsResponse:
    """Bars in an explicit time window from the live (CH) tier.

    USE WHEN: you need bars between specific timestamps at a specific
    interval. For agent-friendly chart queries with auto-windowing
    use `get_bars_for_chart` instead.

    Args:
        symbol: Ticker symbol.
        start: Window start, inclusive. Naive datetimes treated as UTC.
        end: Window end, exclusive.
        interval: '1m', '5m', '15m', '30m', '1h', '4h', '1d'. Anything
            beyond '5m'/'daily' is resampled at query time from
            `ohlcv_1m` (or `ohlcv_5m` if you set source_table).
        limit: Row cap. Default 100k.
        source_table: Force a specific source ('ohlcv_1m' for highest
            fidelity short windows, 'ohlcv_5m' for windows > ~48 days
            where the 1m table doesn't have history). Omit to let the
            reader pick (it always uses ohlcv_1m here; the smart picker
            is in `get_bars_for_chart`).

    Returns:
        LiveBarsResponse with all bars in [start, end).

    Errors: ValueError on unknown interval.
    """
    with tool_call(
        "get_bars_in_range", symbol=symbol, interval=interval,
    ):
        bars = _reader().get_bars_in_range(
            symbol, start, end,
            interval=interval, limit=limit, source_table=source_table,
        )
        return LiveBarsResponse(
            symbol=symbol, interval=interval, bars=bars, count=len(bars),
        )


@mcp.tool()
def get_bars_for_chart(
    symbol: str,
    interval: str = "1m",
    lookback_days: Optional[int] = None,
    limit: Optional[int] = None,
    source: str = "auto",
) -> LiveBarsResponse:
    """Chart-style bar query routed across ClickHouse and the S3 lake.

    USE WHEN: an agent wants "last N days of <interval> for <symbol>"
    without specifying exact timestamps. Routes via the shared bars
    gateway, so this behaves identically to the dashboard's
    `/api/v1/bars` endpoint.

    `source` selects the data tier (S3 lake is ground truth — every
    symbol, full history; ClickHouse is a fast but partial hot cache):
      - 'auto' (default): ClickHouse first; on an empty bounded window,
        fill it from the lake (`equities.polygon_adjusted`) into CH and
        re-query. Self-healing — works for ad-hoc symbols not in the
        streaming universe, and the next call is hot.
      - 'clickhouse': hot cache only. Fast, may be partial. No S3 read.
      - 'lake': S3 ground truth only — complete split-adjusted history,
        resampled to `interval`. Does NOT write CH; use for deep history
        or analysis without warming the local cache.

    Auto-limit (when `limit` omitted): no lookback -> 500 rows; with
    lookback -> ~bars/day * lookback * 1.5, capped at 100k.

    Args:
        symbol: Ticker.
        interval: '1m', '5m', '15m', '30m', '1h', '4h', '1d'.
        lookback_days: Restrict to bars in the last N days. If omitted,
            returns `limit` most-recent bars (CH paths) / last 30d (lake).
        limit: Row cap. Default 500 or auto-sized (see above).
        source: 'auto' | 'clickhouse' | 'lake'. Default 'auto'.

    Returns:
        LiveBarsResponse sorted oldest-first.

    Errors: ValueError on unknown interval or unknown source.
    """
    try:
        src = BarSource(source)
    except ValueError:
        raise ValueError(
            f"Unknown source {source!r}. "
            f"Allowed: {[s.value for s in BarSource]}."
        )
    with tool_call(
        "get_bars_for_chart", symbol=symbol, interval=interval,
        lookback_days=lookback_days, source=src.value,
    ):
        bars = get_chart_bars(
            symbol, interval=interval,
            lookback_days=lookback_days, limit=limit,
            source=src, reader=_reader(),
        )
        return LiveBarsResponse(
            symbol=symbol, interval=interval, bars=bars, count=len(bars),
        )


@mcp.tool()
def get_latest_bar_per_symbol(symbols: list[str]) -> LatestBarsResponse:
    """Most-recent 1-minute bar for each requested symbol.

    USE WHEN: an agent needs a snapshot across many symbols at once —
    "where is each name in my watchlist right now", banner-style
    summaries, current-state input for a screener.

    Args:
        symbols: Up to a few hundred tickers. Symbols with no bars in
            CH are OMITTED from the result (agent should diff against
            requested set to detect gaps).

    Returns:
        LatestBarsResponse with `bars: dict[symbol -> LiveBar]` and
        `count`. Symbol-keyed for O(1) lookup downstream.

    Cost: tens-of-ms even for 100+ symbols (CH `argMax` aggregation
    over a partitioned table).
    """
    with tool_call("get_latest_bar_per_symbol", symbol_count=len(symbols)):
        by_sym = _reader().get_latest_bar_per_symbol(symbols)
        return LatestBarsResponse(bars=by_sym, count=len(by_sym))
