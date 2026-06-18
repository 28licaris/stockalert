"""Iceberg schema + partition spec for the futures lake table.

`futures.schwab_futures` — raw 1-minute OHLCV for CME futures, keyed by
CONTINUOUS ROOT (/ES, /MES, …). Mirrors `equities.schwab_universe` but:
  - NO `adj_factor` column — futures have no splits/dividends.
  - Partition by `month(timestamp)` ONLY (a handful of symbols; bucketing
    would just fan each write across files — same rationale as the
    re-partitioned schwab_universe).
  - Sort order (symbol, timestamp) so single-symbol reads prune well.

Identifier (symbol, timestamp) → idempotent upserts/dedup on read.
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

# The futures we stream + monitor. Continuous roots (front-month). Full
# contracts + their micros. Extend by inserting into stocks.futures_universe.
FUTURES_SEED_ROOTS: list[str] = [
    "/ES", "/MES",    # E-mini / Micro S&P 500
    "/NQ", "/MNQ",    # E-mini / Micro Nasdaq-100
    "/YM", "/MYM",    # E-mini / Micro Dow
    "/RTY", "/M2K",   # E-mini / Micro Russell 2000
    "/GC", "/MGC",    # Gold / Micro Gold
    "/SI", "/SIL",    # Silver / Micro Silver
    "/HG",            # Copper
    "/CL", "/MCL",    # Crude Oil / Micro Crude
    "/NG",            # Natural Gas
]


def futures_table_id(name: str) -> str:
    """Fully-qualified PyIceberg table id (`<futures_glue_db>.<name>`)."""
    return f"{settings.iceberg_futures_glue_database}.{name}"


FUTURES_OHLCV_SCHEMA = Schema(
    NestedField(1, "symbol", StringType(), required=True),        # continuous root, e.g. /ES
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

# month(timestamp) only — see module docstring.
FUTURES_OHLCV_PARTITION = PartitionSpec(
    PartitionField(
        source_id=2, field_id=1000, transform=MonthTransform(), name="ts_month",
    ),
)

FUTURES_OHLCV_SORT = SortOrder(
    SortField(source_id=1, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=2, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
)
