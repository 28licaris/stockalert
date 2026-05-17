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


# ─────────────────────────────────────────────────────────────────────
# Live tier (ClickHouse) — BarReader + SignalReader contracts
# ─────────────────────────────────────────────────────────────────────


class LiveBar(BaseModel):
    """
    One row from ClickHouse `ohlcv_*` (the live tier). Schema parallels
    `BronzeBar` deliberately so consumers can branch on tier and reuse
    most of their code. `interval` distinguishes 1m / 5m / daily and
    resampled variants ('15m', '1h', ...).
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
    source: Optional[str] = None
    interval: str = Field(
        default="1m",
        description="Bar interval: '1m', '5m', '15m', '1h', '4h', 'daily', etc.",
    )


class LiveBarsResponse(BaseModel):
    symbol: str
    interval: str
    bars: list[LiveBar]
    count: int


class LatestBarsResponse(BaseModel):
    """
    Response for `get_latest_bar_per_symbol` — one most-recent bar
    per requested symbol. Used by the market-banner endpoint and by
    agents establishing "where each name is right now."
    """

    bars: dict[str, LiveBar] = Field(
        ...,
        description=(
            "Map from symbol -> the most recent bar in CH. Symbols "
            "with no rows are omitted (callers can diff against the "
            "requested set to find gaps)."
        ),
    )
    count: int


class Signal(BaseModel):
    """One row from CH `signals` — divergence/etc detector output."""

    id: Optional[str] = Field(
        None,
        description="ClickHouse signal row id (stringified UUID).",
    )
    symbol: str
    signal_type: str = Field(
        ...,
        description=(
            "Detector name, e.g. 'hidden_bullish_divergence', "
            "'regular_bearish_divergence'."
        ),
    )
    indicator: str = Field(..., description="'rsi', 'macd', 'tsi', etc.")
    ts_signal: datetime
    price_at_signal: float
    indicator_value: float
    p1_ts: Optional[datetime] = None
    p2_ts: Optional[datetime] = None


class SignalsResponse(BaseModel):
    symbol: Optional[str] = Field(
        None, description="Echoed back when the query was symbol-scoped."
    )
    signals: list[Signal]
    count: int


# ─────────────────────────────────────────────────────────────────────
# QuoteService — provider-quote abstraction (REST, not CH)
# ─────────────────────────────────────────────────────────────────────


class Quote(BaseModel):
    """
    Current quote from whatever provider answered. Some fields are
    provider-specific or unavailable depending on the source — all
    optional except `symbol` and `provider`.
    """

    symbol: str
    last: Optional[float] = None
    bid: Optional[float] = None
    ask: Optional[float] = None
    open: Optional[float] = None
    high: Optional[float] = None
    low: Optional[float] = None
    close: Optional[float] = Field(None, description="Previous close.")
    volume: Optional[float] = None
    timestamp: Optional[datetime] = None
    provider: str = Field(
        ...,
        description="Which provider answered ('schwab', 'polygon', etc.).",
    )


class QuotesResponse(BaseModel):
    quotes: dict[str, Quote]
    count: int
    invalid_symbols: list[str] = Field(
        default_factory=list,
        description="Symbols the provider could not resolve.",
    )


# ─────────────────────────────────────────────────────────────────────
# Discovery + observability schemas (Step 3 Slice 3)
# ─────────────────────────────────────────────────────────────────────


class WatchlistSummary(BaseModel):
    """One watchlist's identity + membership count (no member list)."""

    name: str
    kind: str = Field(..., description="'user', 'default', 'system', etc.")
    description: str = ""
    is_active: bool = True
    member_count: int = 0
    updated_at: Optional[datetime] = None


class WatchlistDetail(BaseModel):
    """A watchlist plus its member symbols."""

    name: str
    kind: str
    description: str = ""
    is_active: bool = True
    members: list[str]
    member_count: int
    updated_at: Optional[datetime] = None


class WatchlistsResponse(BaseModel):
    watchlists: list[WatchlistSummary]
    count: int


class CoverageReport(BaseModel):
    """
    Data-completeness summary for a symbol's bars in a window.

    Used by agents asking "is the training set complete?" before
    running a backtest, or "did we miss data on X day?" before
    investigating a model's bad inference.
    """

    symbol: str
    start: datetime
    end: datetime
    interval: str = Field(..., description="'1m', '5m', '1d' etc.")
    actual_bars: int = Field(..., description="Number of bars present in CH.")
    expected_bars: Optional[int] = Field(
        None,
        description=(
            "Approximate expected bar count for the window at this "
            "interval (regular-session-only basis). None when the "
            "underlying query doesn't compute an estimate."
        ),
    )
    coverage_pct: Optional[float] = Field(
        None,
        description="actual_bars / expected_bars, rounded to 4 decimals. None when expected is unknown.",
    )
    first_bar: Optional[datetime] = None
    last_bar: Optional[datetime] = None


class IntradayGap(BaseModel):
    """One contiguous missing-bar range."""

    start: datetime = Field(..., description="First missing minute (inclusive).")
    end: datetime = Field(..., description="Last missing minute (inclusive).")
    minutes: int = Field(..., description="Length of the gap in minute-bars.")


class GapReport(BaseModel):
    """Intraday gaps for a symbol's bars in a window."""

    symbol: str
    start: datetime
    end: datetime
    interval: str = "1m"
    gaps: list[IntradayGap]
    total_missing_minutes: int


class BronzeTableStats(BaseModel):
    """
    Per-table snapshot of bronze health — row count, file count, last
    snapshot ID, on-disk size estimate. Useful for agents validating
    "is bronze caught up?" before running a training job.
    """

    table_name: str
    namespace: str = "stock_lake"
    total_records: Optional[int] = None
    file_count: Optional[int] = None
    total_size_bytes: Optional[int] = None
    current_snapshot_id: Optional[str] = None
    last_updated: Optional[datetime] = None
    error: Optional[str] = Field(
        None,
        description="Set when the table is unreachable; other fields will be None.",
    )


class LakeFreshnessReport(BaseModel):
    """Bronze-tier freshness: latest trading day per (provider) table."""

    tables: dict[str, Optional[date]] = Field(
        ...,
        description=(
            "Map from table short name (e.g. 'polygon_minute', "
            "'schwab_minute') to its most-recent ET trading day with "
            "≥1 row. Null when the table is empty or unreachable."
        ),
    )
    as_of: datetime


class ServiceStatus(BaseModel):
    """Single subsystem's health snapshot."""

    name: str
    healthy: bool
    detail: Optional[str] = None


class SystemHealthReport(BaseModel):
    """Aggregate system health for agent self-diagnosis."""

    status: str = Field(..., description="'ok' | 'degraded' | 'down'")
    clickhouse: bool
    iceberg_catalog: bool
    services: list[ServiceStatus]
    as_of: datetime
