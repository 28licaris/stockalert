"""
MCP tools backed by `BronzeReader` — historical lake reads.

These are the **CH-independent** tools. They read directly from the
Iceberg bronze lake via S3 + Glue; ClickHouse can be down, redeployed,
or wiped without affecting them. The agent path for ML training,
backtesting, and historical analysis goes through here.

Same Pydantic shapes as `/api/lake/*` HTTP routes — see
`app/services/readers/schemas.py`.

Structural invariant (asserted by `test_mcp_layering.py`):
  no module reachable from this file may import from `app.db.*`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Optional

from app.mcp.middleware import tool_call
from app.mcp.server import mcp
from app.services.readers.bronze_reader import BronzeReader
from app.services.readers.schemas import (
    BronzeBarsResponse,
    LakeLatestDayResponse,
    LakeSymbolsResponse,
)

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _reader() -> BronzeReader:
    """Memoized reader. The catalog under it is already lru_cached."""
    return BronzeReader.from_settings()


@mcp.tool()
def get_bronze_bars(
    symbol: str,
    start: datetime,
    end: datetime,
    provider: str = "polygon",
    limit: Optional[int] = None,
) -> BronzeBarsResponse:
    """Historical OHLCV minute bars from the Iceberg bronze lake.

    USE WHEN: an agent needs historical price data for backtesting,
    feature engineering, training, or "what happened at <time>"
    questions. Works when ClickHouse is down — this is the
    canonical path for ML / training data.

    Args:
        symbol: Ticker symbol, e.g. 'AAPL'.
        start: Window start, inclusive. Naive datetimes treated as UTC.
        end: Window end, exclusive. Half-open interval.
        provider: 'polygon' (5+ years history, default) or 'schwab'
            (~48 days, useful for cross-provider validation).
        limit: Cap on returned bars; returns the MOST RECENT N within
            the window when truncated. Recommended for windows > 1 day
            to bound payload size. Default unlimited.

    Returns:
        BronzeBarsResponse with bars=list[BronzeBar], count=int, plus
        echo of the request (symbol/start/end/provider).

    Cost: sub-second for a per-symbol day. Scales linearly with window
    size and number of bars returned.

    Errors: ValueError if `provider` is unknown.
    """
    with tool_call("get_bronze_bars", symbol=symbol, provider=provider):
        bars = _reader().get_bars(
            symbol, start, end, provider=provider, limit=limit,
        )
        return BronzeBarsResponse(
            symbol=symbol,
            start=start,
            end=end,
            provider=provider,
            bars=bars,
            count=len(bars),
        )


@mcp.tool()
def list_bronze_symbols(
    provider: str = "polygon",
    since: Optional[datetime] = None,
    limit: Optional[int] = None,
) -> LakeSymbolsResponse:
    """Distinct symbols known to bronze within a time window.

    USE WHEN: an agent needs universe discovery — "which tickers does
    the lake have data for", "what symbols have traded since <date>",
    "build my screener candidate set."

    Args:
        provider: 'polygon' or 'schwab'.
        since: Only return symbols with at least one bar at-or-after
            this timestamp. Defaults to 30 days back if omitted (keeps
            the scan bounded against a 2B-row bronze table).
        limit: Cap on number of symbols returned. Symbols are sorted
            alphabetically before truncation.

    Returns:
        LakeSymbolsResponse with symbols=list[str], count, provider,
        and the effective `since` (the resolved default if you didn't
        pass one).

    Cost: 1-5s for a 30-day window. Scales with bar volume in the
    window — narrow the `since` for cheaper queries.
    """
    with tool_call("list_bronze_symbols", provider=provider, since=since):
        symbols = _reader().list_symbols(
            provider=provider, since=since, limit=limit,
        )
        effective_since = since if since is not None else (
            datetime.now(timezone.utc) - timedelta(days=30)
        )
        return LakeSymbolsResponse(
            provider=provider,
            since=effective_since,
            symbols=symbols,
            count=len(symbols),
        )


@mcp.tool()
def get_latest_trading_day(
    provider: str = "polygon",
    lookback_days: int = 14,
) -> LakeLatestDayResponse:
    """Most recent trading day (ET basis) with at least one bar in bronze.

    USE WHEN: an agent needs to anchor a query to "freshest available
    data" without guessing — e.g. "give me the last 5 trading days of
    AAPL" first calls this, then calls `get_bronze_bars` with the
    resolved end date.

    Args:
        provider: 'polygon' or 'schwab'.
        lookback_days: How far back to scan for the most-recent bar.
            Default 14 covers weekends + short holiday gaps. Increase
            only if you suspect bronze hasn't been refreshed in 2+ weeks.

    Returns:
        LakeLatestDayResponse with `latest_trading_day: date | null`.
        Null when no rows exist in the lookback window.

    ET (not UTC) basis: after-hours bars cross midnight UTC, so a UTC
    date would misclassify them and advance the counter early.

    Cost: tens-of-ms. Metadata-only scan via Iceberg's partition stats.
    """
    with tool_call("get_latest_trading_day", provider=provider):
        latest = _reader().latest_trading_day(
            provider=provider, lookback_days=lookback_days,
        )
        return LakeLatestDayResponse(
            provider=provider,
            latest_trading_day=latest,
        )
