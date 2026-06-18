"""
Aggregate price stats — server-computed highs / lows over a window.

The tool for "52-week high", "lowest price in the last N days", "high since
X" — anything that's an aggregate over a window. These MUST be answered with a
single aggregate query, NOT by fetching every bar and scanning: tool results
are truncated to 50 list items before the model sees them, so a row-scan over
a long window silently returns a partial — and wrong — answer. One server-side
``max(high)`` / ``min(low)`` is exact regardless of window size and tiny.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional
from zoneinfo import ZoneInfo

from pydantic import BaseModel

from app.mcp.middleware import tool_call
from app.mcp.server import mcp

_NY = ZoneInfo("America/New_York")


def _et_date(ts: Optional[datetime]) -> Optional[str]:
    """ISO date (ET trading day) for a CH timestamp, or None."""
    if ts is None:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(_NY).date().isoformat()


class PriceStats(BaseModel):
    symbol: str
    lookback_days: int
    period_high: Optional[float] = None
    period_high_date: Optional[str] = None
    period_low: Optional[float] = None
    period_low_date: Optional[str] = None
    last_close: Optional[float] = None
    last_date: Optional[str] = None
    bar_count: int = 0


@mcp.tool()
def get_price_stats(symbol: str, lookback_days: int = 365) -> PriceStats:
    """High / low / last for a symbol over a trailing window, from ClickHouse.

    USE THIS for any aggregate-over-a-window question — "52-week high",
    "lowest price this year", "highest price in the last 90 days". It returns a
    single server-computed row (`max(high)` / `min(low)`), so it is **exact
    regardless of window size**.

    DO NOT answer these by calling `get_bars_*` and scanning the rows yourself:
    bar results are truncated to 50 items, so scanning a long window returns a
    wrong max/min. Use this tool instead.

    Args:
        symbol: Ticker, e.g. 'NVDA'. Futures roots (e.g. '/ES') are supported.
        lookback_days: Trailing window in days. Default 365 (≈ 52 weeks).

    Returns:
        PriceStats with `period_high` / `period_low` (and their ET dates),
        `last_close`, and `bar_count`. `bar_count=0` means ClickHouse has no
        bar for the symbol in the window.

    Cost: a single aggregate scan over recent partitions — tens of ms.
    """
    from app.db import queries
    from app.services.futures.symbols import ch_table_for

    with tool_call("get_price_stats", symbol=symbol, lookback_days=lookback_days):
        table = ch_table_for(symbol)
        s = queries.price_stats(symbol, lookback_days=lookback_days, source_table=table)
        return PriceStats(
            symbol=s["symbol"],
            lookback_days=s["lookback_days"],
            period_high=s.get("period_high"),
            period_high_date=_et_date(s.get("period_high_ts")),
            period_low=s.get("period_low"),
            period_low_date=_et_date(s.get("period_low_ts")),
            last_close=s.get("last_close"),
            last_date=_et_date(s.get("last_ts")),
            bar_count=s["bar_count"],
        )
