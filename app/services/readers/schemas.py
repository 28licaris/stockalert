"""
Pydantic contracts for read services.

These DTOs are the **public interface** for everything that reads market
data — from HTTP routes, MCP tools, and other services. Implementations
in `bronze_reader.py` / `bar_reader.py` / etc. produce these shapes;
consumers depend only on these schemas, never on the implementation
modules.

Why one schema file: an MCP tool wrapping `bronze_reader.get_bars()`
must return exactly the same shape as `/api/lake/bars`. Putting the
contract in one place enforces that.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class BronzeBar(BaseModel):
    """
    One row from `stock_lake.{provider}_minute`.

    Mirrors the canonical bronze schema (see
    `app/services/bronze/schemas.py`). `vwap` and `trade_count` are
    optional because Schwab's pricehistory doesn't provide them — silver
    is where providers get reconciled into a single canonical shape.
    """

    symbol: str
    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float
    vwap: Optional[float] = None
    trade_count: Optional[int] = None
    source: str = Field(
        ...,
        description=(
            "Provider tag from the source row, e.g. 'polygon-flatfiles', "
            "'polygon-rest', 'schwab', 'schwab-stream'. Lets consumers "
            "distinguish ingest paths within a provider."
        ),
    )


class BronzeBarsResponse(BaseModel):
    """
    Response wrapper for a windowed bar query.

    Carries the request echo (`symbol`, `start`, `end`, `provider`)
    alongside the bars so a single object documents what was asked and
    what was returned — useful for caching and for agents that want to
    serialize-and-replay later.
    """

    symbol: str
    start: datetime
    end: datetime
    provider: str
    bars: list[BronzeBar]
    count: int = Field(..., description="Number of bars in `bars`.")


class LakeSymbolsResponse(BaseModel):
    """
    Response for `list_symbols` — distinct tickers known to bronze
    within a time window. Used for universe discovery by screeners,
    agents, and the dashboard.
    """

    provider: str
    since: datetime = Field(
        ...,
        description=(
            "Lower bound of the scan window (inclusive). Defaults to "
            "30 days back if the caller doesn't specify, to keep the "
            "scan tractable against a 2B-row bronze table."
        ),
    )
    symbols: list[str]
    count: int = Field(..., description="Number of distinct symbols.")


class LakeLatestDayResponse(BaseModel):
    """
    Response for `latest_trading_day` — the most recent trading day
    (ET basis) that has at least one bar in bronze. Used by gap
    detectors and by agents establishing "what's the freshest data
    I can train on."
    """

    provider: str
    latest_trading_day: Optional[date] = Field(
        ...,
        description=(
            "ET-basis trading day with at least one row in the bronze "
            "table. Null if no rows in the lookback window. Why ET, "
            "not UTC: after-hours bars cross midnight UTC, so UTC date "
            "misclassifies them and would advance the counter early."
        ),
    )
