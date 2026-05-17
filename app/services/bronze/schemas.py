"""
Bronze Iceberg schemas.

One schema per provider × kind. Schemas drift between providers (Polygon
has trade_count + vwap, Schwab doesn't, etc.) so each gets its own table
and its own schema declaration here.

Naming: lower-snake matching the source Parquet column names so existing
files can be registered via `add_files` without column renames. The
historical Polygon flat-file Parquets use `timestamp` (not `ts`) and we
keep that spelling here for compatibility.
"""
from __future__ import annotations

from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table.sorting import NullOrder, SortDirection, SortField, SortOrder
from pyiceberg.transforms import IdentityTransform, MonthTransform
from pyiceberg.types import (
    DoubleType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

from app.config import settings


def bronze_table_id(name: str) -> str:
    """Fully-qualified Iceberg table identifier (`<glue_db>.<name>`)."""
    return f"{settings.iceberg_glue_database}.{name}"


# ─────────────────────────────────────────────────────────────────────
# bronze.polygon_minute
# ─────────────────────────────────────────────────────────────────────
#
# Mirrors the existing on-disk Parquet schema (minus the
# `__index_level_0__` pandas artifact). Two columns are intentionally
# nullable today and may be populated by future writers:
#   - vwap          → Polygon flat files always emit 0.0. New sinks
#                     write null instead of 0.0. Eventually source
#                     from Polygon REST (which does carry vwap).
#   - ingestion_run_id, ingestion_ts → populated by BronzeIcebergSink;
#                     null for the one-time historical import.
BRONZE_POLYGON_MINUTE_SCHEMA = Schema(
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

BRONZE_POLYGON_MINUTE_PARTITION = PartitionSpec(
    PartitionField(
        source_id=2,
        field_id=1000,
        transform=MonthTransform(),
        name="ts_month",
    ),
)

BRONZE_POLYGON_MINUTE_SORT = SortOrder(
    SortField(source_id=1, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=2, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
)


# ─────────────────────────────────────────────────────────────────────
# bronze.schwab_minute
# ─────────────────────────────────────────────────────────────────────
#
# Same canonical 12-column shape as `bronze.polygon_minute`. Differences
# vs. Polygon at runtime (not in schema):
#   - `vwap`: Schwab pricehistory does not return vwap → always NULL.
#   - `trade_count`: Schwab pricehistory does not return trade counts → NULL.
#   - `source`: "schwab" (Schwab REST pricehistory). If a live-stream path
#     is added later, it should use a distinguishable tag ("schwab-stream").
#
# Keeping the columns even though they're always null lets silver's
# precedence rules treat both providers uniformly without conditional
# schemas.
BRONZE_SCHWAB_MINUTE_SCHEMA = Schema(
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

BRONZE_SCHWAB_MINUTE_PARTITION = PartitionSpec(
    PartitionField(
        source_id=2,
        field_id=1000,
        transform=MonthTransform(),
        name="ts_month",
    ),
)

BRONZE_SCHWAB_MINUTE_SORT = SortOrder(
    SortField(source_id=1, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=2, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
)
