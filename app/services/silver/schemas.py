"""
Silver Iceberg schemas + Pydantic models.

Two layers in one file (kept together so the Python type and the
on-disk Iceberg field IDs travel together):

- **Pydantic models** (`CorpAction`) — the runtime contract; what
  ingestion produces, what readers return, what MCP tools expose.
- **Iceberg schemas** (`SILVER_CORP_ACTIONS_SCHEMA`, etc.) — the
  on-disk layout; what `MERGE INTO` operates on.

Conventions mirror bronze (see [bronze/schemas.py](../bronze/schemas.py)):
- Same field-ID strategy: identifier columns get IDs 1, 2, …; data
  columns follow.
- Glue databases are flat → no real `silver.` namespace; we mimic
  the medallion via the `silver/` S3 prefix + table-name prefix.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table.sorting import (
    NullOrder,
    SortDirection,
    SortField,
    SortOrder,
)
from pyiceberg.transforms import IdentityTransform, MonthTransform, YearTransform
from pyiceberg.types import (
    DateType,
    DoubleType,
    IntegerType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

from app.config import settings


def silver_table_id(name: str) -> str:
    """Fully-qualified Iceberg table identifier (`<glue_db>.<name>`).

    Mirrors `bronze_table_id` — Glue databases are flat so the
    `silver/` separation is purely on-disk (S3 prefix) and via
    table-name prefix.
    """
    return f"{settings.iceberg_glue_database}.{name}"


# ─────────────────────────────────────────────────────────────────────
# Pydantic — the runtime contract
# ─────────────────────────────────────────────────────────────────────


CorpActionKind = Literal[
    "split",                # forward stock split (factor > 1) or reverse (factor < 1)
    "cash_dividend",        # ordinary cash dividend (Polygon dividend_type CD)
    "lt_capital_gain",      # long-term capital-gains distribution (Polygon dividend_type LT)
    "st_capital_gain",      # short-term capital-gains distribution (Polygon dividend_type ST)
    "stock_dividend",       # stock dividend (paid in shares) (Polygon dividend_type SC)
    "spinoff",              # spin-off distribution (Polygon dividend_type SP)
]
# Why these are separate (not collapsed under cash_dividend):
# A fund/ETF can issue MULTIPLE distributions on the same ex_date —
# e.g. an ordinary cash dividend (CD) + a long-term cap-gains
# distribution (LT) + a short-term cap-gains distribution (ST), all
# on the same day. Collapsing them collides on the silver identifier
# (symbol, ex_date, action_type). They're also semantically distinct
# for tax + ML feature purposes. Keep them as separate kinds.


class CorpAction(BaseModel):
    """
    One corporate-action event for one symbol on one ex-date.

    Canonical contract: the same shape produced by ingestion is what
    readers return and what MCP tools expose. Pydantic-validated at
    every boundary.
    """

    symbol: str
    ex_date: date = Field(
        ...,
        description=(
            "Ex-dividend / ex-split date in the issuer's calendar. "
            "Bars on or after this date reflect the corporate-action "
            "effect; bars before need adjustment to compare."
        ),
    )
    action_type: CorpActionKind

    factor: Optional[float] = Field(
        None,
        description=(
            "Split ratio for splits + stock dividends (e.g. 4.0 for a "
            "4-for-1 forward split; 0.5 for a 1-for-2 reverse split; "
            "1.05 for a 5% stock dividend). NULL for cash-only actions."
        ),
    )
    cash_amount: Optional[float] = Field(
        None,
        description=(
            "Dividend per share in USD. NULL for splits. For special "
            "dividends in foreign currencies, converted to USD at the "
            "announcement-date FX rate (TODO — placeholder behavior is "
            "to preserve the original currency value; treat with care)."
        ),
    )

    announced_at: Optional[datetime] = Field(
        None,
        description="When the action was announced (provider-supplied; UTC).",
    )
    source_provider: str = Field(
        default="polygon",
        description=(
            "Canonical source. `polygon` for everything we ingest "
            "today. When alternative corp-action providers are added, "
            "precedence is `polygon > <new>` (config-driven)."
        ),
    )
    ingestion_ts: Optional[datetime] = Field(
        None,
        description="When this silver row was written (UTC).",
    )
    ingestion_run_id: Optional[str] = Field(
        None,
        description=(
            "Run ID linking this row to a CH `ingestion_runs` audit row. "
            "Lets operators answer 'which ingest job produced this corp-action?'"
        ),
    )


# ─────────────────────────────────────────────────────────────────────
# Iceberg — silver.corp_actions
# ─────────────────────────────────────────────────────────────────────
#
# Identifier `(symbol, ex_date, action_type)` is the merge key. Polygon
# occasionally revises announcements (e.g. corrected dividend amount); a
# re-ingest with the same identifier upserts via `MERGE INTO` — the
# downstream silver_build then re-derives adjusted columns from the
# corrected factor.
#
# Partition by `year(ex_date)`: corp-actions are sparse (5K-10K splits
# per year market-wide, 300K-500K dividends) and queries are typically
# either by-symbol (sort order handles this) or by-recent-history
# (year partition handles this). Month partitioning would be overkill.
SILVER_CORP_ACTIONS_SCHEMA = Schema(
    NestedField(1, "symbol", StringType(), required=True),
    NestedField(2, "ex_date", DateType(), required=True),
    NestedField(3, "action_type", StringType(), required=True),
    NestedField(4, "factor", DoubleType(), required=False),
    NestedField(5, "cash_amount", DoubleType(), required=False),
    NestedField(6, "announced_at", TimestamptzType(), required=False),
    NestedField(7, "source_provider", StringType(), required=True),
    NestedField(8, "ingestion_ts", TimestamptzType(), required=False),
    NestedField(9, "ingestion_run_id", StringType(), required=False),
    identifier_field_ids=[1, 2, 3],
)

SILVER_CORP_ACTIONS_PARTITION = PartitionSpec(
    PartitionField(
        source_id=2,                # ex_date
        field_id=1000,
        transform=YearTransform(),
        name="ex_year",
    ),
)

SILVER_CORP_ACTIONS_SORT = SortOrder(
    SortField(
        source_id=1,                # symbol
        transform=IdentityTransform(),
        direction=SortDirection.ASC,
        null_order=NullOrder.NULLS_LAST,
    ),
    SortField(
        source_id=2,                # ex_date
        transform=IdentityTransform(),
        direction=SortDirection.ASC,
        null_order=NullOrder.NULLS_LAST,
    ),
)


# ─────────────────────────────────────────────────────────────────────
# Pydantic — SilverBar (the OHLCV-with-_raw-and-_adj row)
# ─────────────────────────────────────────────────────────────────────


class SilverBar(BaseModel):
    """One canonical 1-minute OHLCV bar in silver.

    Silver stores **split-adjusted** OHLCV. That's the canonical
    consumer view — what chart, indicators, backtests, screener, and
    ML all need (continuous lines across split events, no fake gaps).

    The build pipeline takes per-provider bronze (Polygon = raw,
    Schwab = already split-adjusted) and normalizes everyone to the
    split-adjusted frame via the cumulative-factor math in
    `app/services/silver/ohlcv/normalize.py`.

    **If a consumer needs raw prices** (trade-tape replay, "what was
    the actual fill?"), recompute via:
        raw_value = silver_value × F(symbol, bar_date)
        F = product of split.factor for silver.corp_actions rows
            where action_type='split' AND ex_date > date(bar_ts)
    See `cumulative_factor_after` in normalize.py for reference.
    Silver intentionally does NOT carry redundant `_raw` columns —
    they're derived from the canonical `_adj` plus `silver.corp_actions`.
    """

    symbol: str
    timestamp: datetime

    # OHLCV — split-adjusted. Canonical consumer view.
    open: float
    high: float
    low: float
    close: float
    volume: int

    # Optional provider-supplied fields (NULL in some providers).
    vwap: Optional[float] = None
    trade_count: Optional[int] = None

    # Provenance — which provider won the precedence merge for this cell.
    source_provider: str = Field(
        ...,
        description=(
            "Provider whose bronze row was selected after provider-precedence "
            "merge. The other providers that ALSO had a row for this "
            "(symbol, ts) are in `sources_seen` for QA."
        ),
    )
    sources_seen: list[str] = Field(
        default_factory=list,
        description="Every provider that had a row for this (symbol, ts).",
    )

    ingestion_ts: Optional[datetime] = Field(
        None, description="When silver_build wrote this row (UTC).",
    )
    ingestion_run_id: Optional[str] = Field(
        None, description="silver_build run that produced this row.",
    )


# ─────────────────────────────────────────────────────────────────────
# Iceberg — silver.ohlcv_1m
# ─────────────────────────────────────────────────────────────────────
#
# Per-cell shape: identifier `(symbol, timestamp)`. silver_ohlcv_build
# upserts on this identifier when re-running (idempotent).
#
# Partition `month(timestamp)` matches bronze's strategy — keeps the
# scan plan symmetrical across tiers and makes per-month maintenance
# (compaction, snapshot expiration) consistent.
#
# Sort `(symbol, timestamp)` is the dominant access pattern (one symbol
# over a time window) — same as bronze.
SILVER_OHLCV_1M_SCHEMA = Schema(
    NestedField(1, "symbol", StringType(), required=True),
    NestedField(2, "timestamp", TimestamptzType(), required=True),
    # OHLCV — split-adjusted. Canonical consumer view.
    # If a consumer needs raw prices, recompute via the cumulative
    # split factor from silver.corp_actions (see SilverBar docstring).
    NestedField(3, "open", DoubleType(), required=False),
    NestedField(4, "high", DoubleType(), required=False),
    NestedField(5, "low", DoubleType(), required=False),
    NestedField(6, "close", DoubleType(), required=False),
    NestedField(7, "volume", LongType(), required=False),
    # Optional provider-supplied fields.
    NestedField(8, "vwap", DoubleType(), required=False),
    NestedField(9, "trade_count", LongType(), required=False),
    # Provenance.
    NestedField(10, "source_provider", StringType(), required=True),
    # `sources_seen` deliberately a string column (CSV) rather than
    # array to keep PyIceberg upsert mechanics simple. Cheap to parse on
    # read; cheap to serialize on write. If we ever need fast array
    # filters we can promote to list<string> with a schema migration.
    NestedField(11, "sources_seen", StringType(), required=False),
    NestedField(12, "ingestion_ts", TimestamptzType(), required=False),
    NestedField(13, "ingestion_run_id", StringType(), required=False),
    identifier_field_ids=[1, 2],
)

SILVER_OHLCV_1M_PARTITION = PartitionSpec(
    PartitionField(
        source_id=2,                # timestamp
        field_id=1000,
        transform=MonthTransform(),
        name="ts_month",
    ),
)

SILVER_OHLCV_1M_SORT = SortOrder(
    SortField(
        source_id=1,                # symbol
        transform=IdentityTransform(),
        direction=SortDirection.ASC,
        null_order=NullOrder.NULLS_LAST,
    ),
    SortField(
        source_id=2,                # timestamp
        transform=IdentityTransform(),
        direction=SortDirection.ASC,
        null_order=NullOrder.NULLS_LAST,
    ),
)


# ─────────────────────────────────────────────────────────────────────
# Iceberg — silver.bar_quality
# ─────────────────────────────────────────────────────────────────────
#
# One row per (symbol, date) — the audit ledger for silver_ohlcv_build.
# Catches silent provider drops, schema drifts, and cross-provider
# disagreements. Pinned by silver_layer_plan §3.1 (and §6 of
# data_platform_plan).
#
# Identifier `(symbol, date)` is the merge key; re-running silver_build
# for a date upserts this row.
SILVER_BAR_QUALITY_SCHEMA = Schema(
    NestedField(1, "symbol", StringType(), required=True),
    NestedField(2, "date", DateType(), required=True),
    NestedField(3, "expected_bars", IntegerType(), required=False),
    NestedField(4, "actual_bars", IntegerType(), required=False),
    NestedField(5, "gap_count", IntegerType(), required=False),
    NestedField(6, "max_gap_minutes", IntegerType(), required=False),
    # CSV of providers that had at least one bar this day (e.g.
    # "polygon,schwab"). String for upsert simplicity; same rationale
    # as sources_seen above.
    NestedField(7, "providers_seen", StringType(), required=False),
    NestedField(8, "disagreement_count", IntegerType(), required=False),
    NestedField(9, "backfill_attempts", IntegerType(), required=False),
    NestedField(10, "ingestion_ts", TimestamptzType(), required=False),
    NestedField(11, "ingestion_run_id", StringType(), required=False),
    identifier_field_ids=[1, 2],
)

SILVER_BAR_QUALITY_PARTITION = PartitionSpec(
    PartitionField(
        source_id=2,                # date
        field_id=1000,
        transform=MonthTransform(),
        name="date_month",
    ),
)

SILVER_BAR_QUALITY_SORT = SortOrder(
    SortField(
        source_id=1, transform=IdentityTransform(),
        direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST,
    ),
    SortField(
        source_id=2, transform=IdentityTransform(),
        direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST,
    ),
)
