"""
MCP tools for indicator computation — agent-facing surface.

Thin adapters over `IndicatorReader`. Identical Pydantic shapes as
the HTTP routes in `app/api/routes_indicators.py`. Same math, same
numbers, regardless of whether the consumer is an LLM agent
(MCP) or a dashboard (HTTP).

Full design: `docs/indicator_exposure_design.md` §4.5.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Optional

from app.mcp.middleware import tool_call
from app.mcp.server import mcp
from app.services.readers.indicator_reader import IndicatorReader
from app.services.readers.schemas import (
    IndicatorChartData,
    IndicatorSeries,
)

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _reader() -> IndicatorReader:
    return IndicatorReader.from_settings()


@mcp.tool()
def compute_indicator(
    symbol: str,
    indicator: str,
    start: datetime,
    end: datetime,
    interval: str = "1d",
    params: Optional[dict[str, Any]] = None,
    provider: str = "polygon",
) -> IndicatorSeries:
    """Compute one indicator series for a symbol over a window.

    USE WHEN: an agent needs a single named indicator —
    "what's AAPL's RSI(14) over the last quarter?" "Give me a
    20-day SMA of SPY for backtest scaffolding." Returns one
    `IndicatorSeries` with timestamped values.

    Args:
        symbol: Ticker.
        indicator: Registry name — 'sma', 'ema', 'wma', 'rsi',
            'macd', 'tsi', 'stochastic', 'atr', 'bollinger'.
            See `compute_indicators` if you want multi-output
            components from Bollinger / Stochastic / MACD —
            this single-call returns only the canonical output.
        start: Window start, inclusive. Naive datetimes treated as UTC.
        end: Window end, exclusive.
        interval: '1m' (bronze) | '5m' | '15m' | '30m' | '1h' | '4h' | '1d' (CH).
        params: Indicator constructor kwargs as a dict
            (e.g. `{"period": 20}` for SMA, `{"period": 20,
            "std_multiplier": 2.0}` for Bollinger). Empty → defaults.
        provider: Bronze provider (only used when interval='1m').

    Returns:
        IndicatorSeries with values=[{timestamp, value}, ...]. Nulls
        during warmup or NaN-guard windows (zero-range Stochastic,
        etc).

    Cost: <100ms typical. Latency dominated by bar fetch (Iceberg
    partition prune ~50-200ms for a 1y window; CH live read ~10ms).
    """
    with tool_call("compute_indicator", symbol=symbol, indicator=indicator):
        return _reader().get_series(
            symbol=symbol,
            indicator=indicator,
            params=params or {},
            start=start, end=end,
            interval=interval, provider=provider,
        )


@mcp.tool()
def compute_indicators(
    symbol: str,
    indicators: list[dict[str, Any]],
    start: datetime,
    end: datetime,
    interval: str = "1d",
    provider: str = "polygon",
) -> IndicatorChartData:
    """Compute multiple indicators + return bars in one bundle.

    USE WHEN: an agent wants a chart-style payload — "show me
    AAPL daily with SMA(20), SMA(50), RSI(14), and Bollinger(20, 2)."
    Single round-trip beats N separate `compute_indicator` calls.

    Multi-output indicators (Bollinger / Stochastic / MACD) decompose
    into one IndicatorSeries per component in `series`. So one
    'bollinger' spec yields five entries (upper / middle / lower /
    bandwidth / percent_b).

    Args:
        symbol: Ticker.
        indicators: List of dicts, each shape:
            `{"name": "<registry name>", "params": {...}, "label": "..."}`.
            `label` is optional; defaults to a sensible 'SMA(20)' form.
        start, end: Window. Naive datetimes treated as UTC.
        interval: Bar interval (see compute_indicator).
        provider: Bronze provider for 1m.

    Returns:
        `IndicatorChartData` with bars + series + (when bronze-backed)
        snapshot_id for reproducibility.

    Cost: still <200ms for 5 indicators × 200 bars; scales with bar
    count, not indicator count (indicators are O(bars) and pandas
    is fast).
    """
    with tool_call("compute_indicators", symbol=symbol, n_indicators=len(indicators)):
        return _reader().get_chart_data(
            symbol=symbol,
            indicator_specs=indicators,
            start=start, end=end,
            interval=interval, provider=provider,
        )


@mcp.tool()
def get_chart_data(
    symbol: str,
    interval: str = "1d",
    lookback_days: int = 90,
    indicators: Optional[list[dict[str, Any]]] = None,
    provider: str = "polygon",
) -> IndicatorChartData:
    """Bars + indicator overlays for a relative-lookback window.

    USE WHEN: an agent wants chart data anchored to "now minus N
    days" without specifying explicit timestamps. Saves a round
    trip to `get_latest_trading_day`.

    Args:
        symbol: Ticker.
        interval: Bar interval.
        lookback_days: Window size (default 90). End is now (UTC);
            start = end - lookback_days.
        indicators: Optional indicator specs. Empty list / None →
            bars only (no overlays).
        provider: Bronze provider for 1m.

    Returns:
        IndicatorChartData with bars + N series.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)
    return compute_indicators(
        symbol=symbol,
        indicators=indicators or [],
        start=start, end=end,
        interval=interval, provider=provider,
    )
