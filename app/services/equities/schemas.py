"""
Architecture-v2 equities Iceberg schemas (Phase 1 / CV1).

Four tables in the `equities` Glue database (Gate 1, namespace decision):

  - polygon_raw         — whole-market 5y Polygon flat-files, unadjusted
  - polygon_adjusted    — same rows + corp-action adjustment math; carries
                          `adj_factor` (Gate 2)
  - schwab_universe     — live + REST tip-fill Schwab bars; already
                          adjusted at the API, `adj_factor` defaults to 1.0
  - market_corp_actions — whole-market splits/dividends from Polygon REST

Canonical OHLCV columns (symbol, timestamp, open, high, low, close,
volume, vwap, trade_count, source) plus operational columns
(`ingestion_ts`, `ingestion_run_id`) carried forward from the v1 bronze
pattern so audit trails keep working. `adj_factor` lives ONLY on the
two adjusted tables — raw is unadjusted by definition.

Partitioning (Gate 3):
  - polygon_raw / polygon_adjusted: bucket(32, symbol), month(timestamp)
  - schwab_universe:                 bucket(16, symbol), month(timestamp)
  - market_corp_actions:             month(ex_date) only

Spec: docs/architecture_v2/02_schema.md + docs/architecture_v2/08_decisions.md.
"""
from __future__ import annotations

from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table.sorting import NullOrder, SortDirection, SortField, SortOrder
from pyiceberg.transforms import BucketTransform, IdentityTransform, MonthTransform
from pyiceberg.types import (
    DateType,
    DoubleType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

from app.config import settings


def equities_table_id(name: str) -> str:
    """Fully-qualified PyIceberg table identifier (`<glue_db>.<name>`).

    Maps to the v2 spec's `lake.equities.<name>` Spark/DuckDB form when
    the catalog is configured with name `lake`.
    """
    return f"{settings.iceberg_equities_glue_database}.{name}"


# Gate 3 — bucket count (docs/architecture_v2/08_decisions.md#gate-3).
# Only polygon_adjusted (whole 33K-symbol market) is bucketed;
# schwab_universe partitions by month(timestamp) only — see its
# partition spec below for the rationale.
POLYGON_BUCKET_COUNT = 32


# ─────────────────────────────────────────────────────────────────────
# equities.polygon_raw — Polygon flat-files, RAW (unadjusted)
# ─────────────────────────────────────────────────────────────────────
#
# Field IDs preserved from the v1 bronze.polygon_minute schema so the
# Athena bulk-import (scripts/lake_import_athena.py) can copy data
# without a schema-evolution rewrite. `adj_factor` is intentionally
# absent — raw is unadjusted by definition (02_schema.md).
POLYGON_RAW_SCHEMA = Schema(
    NestedField(1, "symbol", StringType(), required=True),
    NestedField(2, "timestamp", TimestamptzType(), required=True),
    NestedField(3, "open", DoubleType(), required=False),
    NestedField(4, "high", DoubleType(), required=False),
    NestedField(5, "low", DoubleType(), required=False),
    NestedField(6, "close", DoubleType(), required=False),
    NestedField(7, "volume", DoubleType(), required=False),
    NestedField(8, "vwap", DoubleType(), required=False),
    NestedField(9, "trade_count", LongType(), required=False),
    NestedField(10, "source", StringType(), required=False),
    NestedField(11, "ingestion_ts", TimestamptzType(), required=False),
    NestedField(12, "ingestion_run_id", StringType(), required=False),
    identifier_field_ids=[1, 2],
)

POLYGON_RAW_PARTITION = PartitionSpec(
    PartitionField(
        source_id=1,
        field_id=1000,
        transform=BucketTransform(POLYGON_BUCKET_COUNT),
        name="symbol_bucket",
    ),
    PartitionField(
        source_id=2,
        field_id=1001,
        transform=MonthTransform(),
        name="ts_month",
    ),
)

POLYGON_RAW_SORT = SortOrder(
    SortField(source_id=1, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=2, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
)


# ─────────────────────────────────────────────────────────────────────
# equities.polygon_daily_raw — Polygon REST grouped-daily, RAW (unadjusted)
# ─────────────────────────────────────────────────────────────────────
#
# One row per (symbol, trading day) for the WHOLE US market (~12.4k
# symbols/day, ~3M rows/yr — tiny next to the minute lake). Source of
# truth for daily bars after the flat-files subscription lapsed
# (2026-07-01): `/v2/aggs/grouped` needs only the REST key, one call/day.
# Same canonical OHLCV shape as polygon_raw; unadjusted by definition.
# No symbol bucketing — the table is small enough that month pruning
# alone serves both per-day appends and per-symbol reads.
POLYGON_DAILY_RAW_SCHEMA = Schema(
    NestedField(1, "symbol", StringType(), required=True),
    NestedField(2, "timestamp", TimestamptzType(), required=True),
    NestedField(3, "open", DoubleType(), required=False),
    NestedField(4, "high", DoubleType(), required=False),
    NestedField(5, "low", DoubleType(), required=False),
    NestedField(6, "close", DoubleType(), required=False),
    NestedField(7, "volume", DoubleType(), required=False),
    NestedField(8, "vwap", DoubleType(), required=False),
    NestedField(9, "trade_count", LongType(), required=False),
    NestedField(10, "source", StringType(), required=False),
    NestedField(11, "ingestion_ts", TimestamptzType(), required=False),
    NestedField(12, "ingestion_run_id", StringType(), required=False),
    identifier_field_ids=[1, 2],
)

POLYGON_DAILY_RAW_PARTITION = PartitionSpec(
    PartitionField(
        source_id=2,
        field_id=1001,
        transform=MonthTransform(),
        name="ts_month",
    ),
)

POLYGON_DAILY_RAW_SORT = SortOrder(
    SortField(source_id=1, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=2, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
)


# ─────────────────────────────────────────────────────────────────────
# equities.polygon_adjusted — RETIRED (lean storage migration)
# ─────────────────────────────────────────────────────────────────────
#
# The materialized adjusted table was dropped: adjusted OHLCV is a pure
# function of polygon_raw + market_corp_actions splits, so it's computed at
# read time via app.services.equities.adjust.apply_adjustment instead of
# stored as a second ~2.1B-row copy. See docs/adjusted_lean_storage_spec.md.
# No POLYGON_ADJUSTED_SCHEMA / _PARTITION / _SORT — there is no table.


# ─────────────────────────────────────────────────────────────────────
# equities.schwab_universe — Schwab live + REST tip-fill (adjusted)
# ─────────────────────────────────────────────────────────────────────
#
# Schwab returns split-adjusted prices at the API. `adj_factor` is
# always 1.0 (cannot be backed out from Schwab data — Schwab does not
# expose pre-split prices). The column exists for schema parity with
# polygon_adjusted so UNION queries don't need column massaging
# (02_schema.md, "Cross-table queries — the schema parity payoff").
SCHWAB_UNIVERSE_SCHEMA = Schema(
    NestedField(1, "symbol", StringType(), required=True),
    NestedField(2, "timestamp", TimestamptzType(), required=True),
    NestedField(3, "open", DoubleType(), required=False),
    NestedField(4, "high", DoubleType(), required=False),
    NestedField(5, "low", DoubleType(), required=False),
    NestedField(6, "close", DoubleType(), required=False),
    NestedField(7, "volume", DoubleType(), required=False),
    NestedField(8, "vwap", DoubleType(), required=False),
    NestedField(9, "trade_count", LongType(), required=False),
    NestedField(10, "source", StringType(), required=False),
    NestedField(11, "ingestion_ts", TimestamptzType(), required=False),
    NestedField(12, "ingestion_run_id", StringType(), required=False),
    NestedField(13, "adj_factor", DoubleType(), required=True),
    identifier_field_ids=[1, 2],
)

# Partition by month(timestamp) ONLY — no symbol bucketing.
# schwab_universe holds the recent rolling window of the *active* universe
# (~hundreds of symbols), not the whole market. With a nightly single-file
# write + the sort order below (symbol-clustered files), a month-only
# layout yields ~1 file/month and needs no compaction; a single-symbol
# query month-prunes then uses Parquet symbol-column stats. Bucketing
# (like polygon_adjusted's bucket(32) for 33K symbols) only pays off once
# a month partition exceeds ~0.5-1 GB (low-thousands of symbols); below
# that it just fans every write across N files. Re-add bucket(symbol) if
# the universe ever grows into the low thousands.
SCHWAB_UNIVERSE_PARTITION = PartitionSpec(
    PartitionField(
        source_id=2,
        field_id=1001,
        transform=MonthTransform(),
        name="ts_month",
    ),
)

SCHWAB_UNIVERSE_SORT = SortOrder(
    SortField(source_id=1, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=2, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
)


# ─────────────────────────────────────────────────────────────────────
# equities.market_corp_actions — Polygon REST corp-actions
# ─────────────────────────────────────────────────────────────────────
#
# Whole-market splits + dividends. `factor` is the split ratio
# (e.g. 4.0 for 4-for-1); null on dividend rows. `cash_amount` is the
# per-share USD payout; null on split rows. Identifier columns match
# v1's bronze.polygon_corp_actions for migration ease. The `raw_payload`
# JSON column lets us re-parse if Polygon's schema ever drifts.
MARKET_CORP_ACTIONS_SCHEMA = Schema(
    NestedField(1, "symbol", StringType(), required=True),
    NestedField(2, "ex_date", DateType(), required=True),
    NestedField(3, "action_type", StringType(), required=True),
    NestedField(4, "factor", DoubleType(), required=False),
    NestedField(5, "cash_amount", DoubleType(), required=False),
    NestedField(6, "announced_at", TimestamptzType(), required=False),
    NestedField(7, "source_provider", StringType(), required=True),
    NestedField(8, "raw_payload", StringType(), required=False),
    NestedField(9, "ingestion_ts", TimestamptzType(), required=False),
    NestedField(10, "ingestion_run_id", StringType(), required=False),
    identifier_field_ids=[1, 2, 3],
)

MARKET_CORP_ACTIONS_PARTITION = PartitionSpec(
    PartitionField(
        source_id=2,
        field_id=1000,
        transform=MonthTransform(),
        name="ex_month",
    ),
)

MARKET_CORP_ACTIONS_SORT = SortOrder(
    SortField(source_id=1, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=2, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
)


# ─────────────────────────────────────────────────────────────────────
# equities.market_splits — dedicated splits store (adjustment input)
# ─────────────────────────────────────────────────────────────────────
#
# Splits ONLY (factor != 1.0). Separated from market_corp_actions (~3M
# dividend rows) so the split lookup the read-time adjustment needs is
# cheap: the whole set is ~50k rows, so a full read is sub-second and a
# per-symbol read is instant. Sorted by (symbol, ex_date); unpartitioned
# (the table is tiny). See docs/market_splits_spec.md.
MARKET_SPLITS_SCHEMA = Schema(
    NestedField(1, "symbol", StringType(), required=True),
    NestedField(2, "ex_date", DateType(), required=True),
    NestedField(3, "factor", DoubleType(), required=True),
    NestedField(4, "source_provider", StringType(), required=False),
    NestedField(5, "ingestion_ts", TimestamptzType(), required=False),
    NestedField(6, "ingestion_run_id", StringType(), required=False),
    identifier_field_ids=[1, 2],
)

# Tiny table → unpartitioned. The (symbol, ex_date) sort + small files give
# fast per-symbol pruning AND fast full scans without partition overhead.
MARKET_SPLITS_PARTITION = PartitionSpec()

MARKET_SPLITS_SORT = SortOrder(
    SortField(source_id=1, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=2, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
)
