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


# Gate 3 — bucket counts (docs/architecture_v2/08_decisions.md#gate-3).
POLYGON_BUCKET_COUNT = 32
SCHWAB_BUCKET_COUNT = 16


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
# equities.polygon_adjusted — Polygon raw + corp-actions applied
# ─────────────────────────────────────────────────────────────────────
#
# Same shape as polygon_raw plus `adj_factor` (Gate 2). Field ID 13
# is reserved for adj_factor so a later schema-evolution migration that
# adds a column here won't collide. NOT NULL — every adjusted row
# carries its cumulative future-splits factor (1.0 means no future
# splits at the bar's timestamp).
POLYGON_ADJUSTED_SCHEMA = Schema(
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

POLYGON_ADJUSTED_PARTITION = PartitionSpec(
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

POLYGON_ADJUSTED_SORT = SortOrder(
    SortField(source_id=1, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=2, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
)


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

SCHWAB_UNIVERSE_PARTITION = PartitionSpec(
    PartitionField(
        source_id=1,
        field_id=1000,
        transform=BucketTransform(SCHWAB_BUCKET_COUNT),
        name="symbol_bucket",
    ),
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
