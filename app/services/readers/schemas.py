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
from typing import Any, Optional

from pydantic import BaseModel, Field

from app.services.equities.models import CorpAction, SilverBar


class BronzeBar(BaseModel):
    """
    One row from a v2 equities OHLCV table (post-CV14: polygon_raw
    or schwab_universe; class name preserved through CV14 for caller
    compatibility, will rename to LakeBar in a follow-up).

    Mirrors the canonical column shape used by both v2 lake tables.
    `vwap` and `trade_count` are optional because Schwab's
    pricehistory doesn't provide them — the v2 schwab_universe rows
    have NULL there; polygon_raw / polygon_adjusted populate them.
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


class CorpActionsResponse(BaseModel):
    """Response wrapper for a windowed corp-actions query.

    Reads from `silver.corp_actions` — the canonical consumer surface
    per the medallion contract. Callers never read bronze corp-actions
    directly; silver build merges providers with precedence and
    publishes here.
    """

    symbol: str
    since: Optional[date] = Field(
        None,
        description="Lower bound on ex_date (inclusive). None = full history.",
    )
    until: Optional[date] = Field(
        None,
        description="Upper bound on ex_date (inclusive). None = through today.",
    )
    action_types: Optional[list[str]] = Field(
        None,
        description=(
            "Filter to specific action kinds (split, cash_dividend, "
            "stock_dividend, spinoff). None = all kinds."
        ),
    )
    snapshot_id: Optional[str] = Field(
        None,
        description=(
            "Iceberg snapshot pinned by the read. Recording this lets "
            "callers replay the same query against the same lake state."
        ),
    )
    actions: list[CorpAction]
    count: int = Field(..., description="Number of actions returned.")


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


# ─────────────────────────────────────────────────────────────────────
# Indicator exposure schemas (TA-3.2)
# See docs/indicator_exposure_design.md for the full architectural
# rationale. These are the canonical shapes used by the HTTP routes
# in `app/api/routes_indicators.py` AND the MCP tools in
# `app/mcp/tools/indicators.py` — single contract, two surfaces.
# ─────────────────────────────────────────────────────────────────────


class IndicatorValue(BaseModel):
    """
    One (timestamp, value) pair from an indicator series.

    `value` is `None` during warmup or wherever the indicator
    returned NaN (e.g. zero-range Stochastic window, divide-by-zero
    guarded math). Consumers MUST handle nulls — strategies and
    charts both skip them.
    """

    timestamp: datetime
    value: Optional[float] = None


class IndicatorSeries(BaseModel):
    """
    A single named series of computed indicator values aligned to
    its bar window.

    Naming convention:
      - Single-output indicators (SMA, EMA, RSI, ATR, …) use the
        indicator name directly: `"sma"`, `"rsi_14"`, etc.
      - Multi-output indicators (Bollinger, Stochastic, MACD)
        decompose into one IndicatorSeries per component, suffixed
        with the component name:
          - Bollinger -> `bollinger_upper`, `bollinger_middle`,
            `bollinger_lower`, `bollinger_bandwidth`,
            `bollinger_percent_b`.
          - Stochastic -> `stochastic_k`, `stochastic_d`.
          - MACD -> `macd`, `macd_signal`, `macd_histogram`.
        This is what the `IndicatorReader` produces; consumers see
        one series at a time regardless of indicator arity.
    """

    name: str = Field(
        ...,
        description="Series identifier, e.g. 'sma', 'bollinger_upper'.",
    )
    params: dict[str, Any] = Field(
        default_factory=dict,
        description="Indicator parameters echoed for traceability.",
    )
    label: str = Field(
        ...,
        description="Display label for charts: 'SMA(20)', 'BB Upper(20, 2.0)', etc.",
    )
    values: list[IndicatorValue]
    count: int = Field(..., description="len(values). Echoed for cheap client-side checks.")


class IndicatorChartData(BaseModel):
    """
    Bundle of OHLCV bars + one or more indicator series, all aligned
    to the same time window. Canonical response for chart endpoints
    and agent batch queries.

    The `bars` list is the same `BronzeBar` shape returned by
    `/api/lake/bars`. The harness converts CH `LiveBar` to
    `BronzeBar` when the interval is non-bronze so the response
    shape stays uniform across data sources.
    """

    symbol: str
    interval: str = Field(
        ...,
        description="'1m', '5m', '15m', '30m', '1h', '4h', '1d'.",
    )
    start: datetime
    end: datetime
    bars: list[BronzeBar]
    series: list[IndicatorSeries]
    snapshot_id: Optional[str] = Field(
        None,
        description=(
            "Iceberg snapshot_id pinned at fetch time when reading "
            "from bronze (1m interval). None when reading from CH "
            "(other intervals) — CH has no snapshot semantics."
        ),
    )


# ─────────────────────────────────────────────────────────────────────
# Silver OHLCV (TA-5.1.5) — canonical consumer surface for 1m bars
# ─────────────────────────────────────────────────────────────────────
#
# These response wrappers are what the HTTP route (`/api/silver/bars/...`)
# and the MCP tool (`get_silver_bars`) both surface. Same Pydantic
# contract on both surfaces. Reads `silver.ohlcv_1m` — the
# provider-merged, corp-action-adjusted, dedup'd canonical OHLCV.


class SilverBarsResponse(BaseModel):
    """Response wrapper for a windowed silver-OHLCV query.

    Reads `silver.ohlcv_1m`. Every row carries BOTH `_raw` (what the
    provider sent) and `_adj` (split + cash-dividend back-adjusted)
    columns — consumers pick which they want per the
    [silver_layer_plan consumer contract](../../../docs/silver_layer_plan.md).
    Default consumption is `_adj` (chart, screener, indicators,
    backtest, ML); `_raw` is for replay-accuracy / trade-tape reconstruction.

    `snapshot_id` is the Iceberg snapshot pinned by the read.
    Recording this lets callers replay against the exact lake state.
    """

    symbol: str
    start: datetime
    end: datetime
    snapshot_id: Optional[str] = Field(
        None,
        description=(
            "Iceberg snapshot pinned by the read. None if the silver "
            "table doesn't exist yet (cold-start before first build)."
        ),
    )
    bars: list[SilverBar]
    count: int = Field(..., description="len(bars). Echoed for cheap client-side checks.")


class BarQualityRow(BaseModel):
    """One row from `silver.bar_quality` — the per-(symbol, date) audit
    ledger produced alongside silver.ohlcv_1m.

    Used by:
      - Operators inspecting nightly silver-build health.
      - Agents asking "did my training set have any silent gaps on day X?"
      - The dashboard's data-quality panel.
    """

    symbol: str
    date: date
    expected_bars: Optional[int] = Field(
        None,
        description=(
            "RTH minutes for the trading day (390 by default). Per-symbol "
            "override possible for ETFs / ADRs with non-standard hours."
        ),
    )
    actual_bars: Optional[int] = Field(
        None, description="Distinct minute timestamps observed in silver.",
    )
    gap_count: Optional[int] = Field(
        None,
        description=(
            "Number of contiguous missing-minute runs within the day. "
            "Each absent block counts as one gap regardless of length."
        ),
    )
    max_gap_minutes: Optional[int] = Field(
        None, description="Length of the longest single gap (in minutes).",
    )
    providers_seen: list[str] = Field(
        default_factory=list,
        description="Providers that contributed at least one bar this day.",
    )
    disagreement_count: Optional[int] = Field(
        None,
        description=(
            "Number of minutes where two or more providers' close prices "
            "disagreed beyond the tolerance (50¢ OR 0.5%)."
        ),
    )
    backfill_attempts: Optional[int] = Field(
        None,
        description=(
            "Re-tries the orchestrator performed for this slice. 0 for a "
            "clean first-shot build; rises when nightly retries kick in."
        ),
    )
    ingestion_ts: Optional[datetime] = Field(
        None, description="When silver_ohlcv_build wrote this row (UTC).",
    )
    ingestion_run_id: Optional[str] = Field(
        None,
        description="Run ID linking this row to the silver-build invocation.",
    )


class BarQualityResponse(BaseModel):
    """Response wrapper for a windowed bar-quality query."""

    symbol: str
    since: Optional[date] = Field(
        None, description="Lower bound on `date` (inclusive). None = all history.",
    )
    until: Optional[date] = Field(
        None, description="Upper bound on `date` (inclusive). None = through today.",
    )
    snapshot_id: Optional[str] = Field(
        None,
        description=(
            "Iceberg snapshot pinned by the read. None if silver.bar_quality "
            "doesn't exist yet."
        ),
    )
    rows: list[BarQualityRow]
    count: int


class SourceCoverage(BaseModel):
    """Per-table coverage stats for one symbol (CV26).

    Returned twice in a SymbolCoverageResponse — once for
    `equities.polygon_adjusted` and once for `equities.schwab_universe`
    — so consumers can see which source has which window.
    """

    table_name: str = Field(
        ...,
        description=(
            "Fully-qualified Iceberg table id (e.g. "
            "'equities.polygon_adjusted'). Echoed so consumers caching "
            "this response don't have to guess where the numbers came "
            "from."
        ),
    )
    row_count: int = Field(
        ..., description="Total rows for this (symbol, table). 0 = symbol absent.",
    )
    earliest_timestamp: Optional[datetime] = Field(
        None,
        description=(
            "Min(timestamp) UTC over all rows for this symbol in the "
            "table. None when row_count=0."
        ),
    )
    latest_timestamp: Optional[datetime] = Field(
        None,
        description=(
            "Max(timestamp) UTC. None when row_count=0. The lag between "
            "this and 'now' is the staleness gauge for this source — "
            "polygon_adjusted lags by up to one weekly Spark run; "
            "schwab_universe is live-cadence."
        ),
    )
    snapshot_id: Optional[str] = Field(
        None,
        description=(
            "Iceberg snapshot pinned by the scan. Lets a follow-up "
            "call replay against the exact lake state. None when the "
            "table doesn't exist yet (cold-start)."
        ),
    )


class SymbolCoverageResponse(BaseModel):
    """What the v2 lake knows about `symbol` (CV26).

    Operator + agent surface for the most common investigation
    question: "do we have data for AAPL, how far back, and how
    current?" Returns coverage for both v2 adjusted-OHLCV sources;
    the consumer decides which one matters for their use case
    (deep history → polygon_adjusted; live + tip-fill →
    schwab_universe).
    """

    symbol: str
    polygon_adjusted: SourceCoverage
    schwab_universe: SourceCoverage


class CrossProviderDiffRow(BaseModel):
    """One (symbol, timestamp) point where the two adjusted sources
    disagree beyond the caller's tolerance (CV27)."""

    timestamp: datetime
    polygon_close: float
    schwab_close: float
    abs_diff: float = Field(
        ...,
        description="abs(polygon_close - schwab_close)",
    )
    pct_diff: float = Field(
        ...,
        description=(
            "(polygon_close - schwab_close) / polygon_close. "
            "Positive: polygon is higher. Caller compares against "
            "their tolerance to decide whether to surface."
        ),
    )


class CrossProviderDiffResponse(BaseModel):
    """Per-(symbol, timestamp) close-price disagreements between
    `equities.polygon_adjusted` and `equities.schwab_universe` in
    `[start, end)`, filtered to rows whose abs_pct_diff exceeds
    `tolerance` (CV27).

    Use this to answer:
      - "Did a corp-action correction land in polygon but not schwab yet?"
      - "Is my strategy's bad day a data bug or a real signal?"
      - "Should I trust today's close for this symbol?"

    Only timestamps present in BOTH sources are compared — single-
    sided rows are by construction not disagreements. The matched
    timestamp count is reported via `compared_count` so the consumer
    can tell "no disagreements vs nothing compared".

    Polygon is the canonical adjusted source; sign convention on
    `pct_diff` is polygon-minus-schwab so callers can compare
    against directional tolerances.
    """

    symbol: str
    start: datetime
    end: datetime
    tolerance: float = Field(
        ...,
        description=(
            "Caller's threshold on |pct_diff|. Rows are surfaced only "
            "when abs(pct_diff) > tolerance. Typical: 0.005 (50bps)."
        ),
    )
    compared_count: int = Field(
        ...,
        description=(
            "Number of (symbol, timestamp) pairs present in BOTH "
            "sources. The denominator for any QA conclusion."
        ),
    )
    disagreements: list[CrossProviderDiffRow]
    count: int = Field(
        ..., description="len(disagreements) — convenience.",
    )
