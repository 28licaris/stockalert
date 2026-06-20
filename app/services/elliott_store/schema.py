"""Iceberg schema + row mapping for `<ns>.elliott_wave_labels`.

One append-only row per (symbol, interval, as_of_date, engine_ver) — the wave
count *as known on that trading day* (the no-look-ahead store contract from
docs/elliott_wave_system_spec.md §3). Mirrored across the `equities` and
`futures` Glue namespaces; `asset_class` + the table namespace are the only
things that differ between the two.

Primary and secondary counts are first-class columns (the product surfaces
exactly two paths); `p_targets`/`p_pivots` carry the structured detail as JSON.
`engine_ver` + `git_sha` make every row reproducible.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Optional

import pyarrow as pa
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table.sorting import NullOrder, SortDirection, SortField, SortOrder
from pyiceberg.transforms import IdentityTransform, MonthTransform
from pyiceberg.types import (
    DateType,
    DoubleType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

from app.config import settings
from app.signals.elliott.schemas import WaveCandidate, WaveLabeling

TABLE_NAME = "elliott_wave_labels"

ELLIOTT_WAVE_LABELS_SCHEMA = Schema(
    NestedField(1, "symbol", StringType(), required=True),
    NestedField(2, "as_of_date", DateType(), required=True),
    NestedField(3, "interval", StringType(), required=True),
    NestedField(4, "as_of_ts", TimestamptzType(), required=False),
    NestedField(5, "asset_class", StringType(), required=False),
    NestedField(28, "as_of_price", DoubleType(), required=False),
    # primary count
    NestedField(6, "p_structure", StringType(), required=False),
    NestedField(7, "p_direction", StringType(), required=False),
    NestedField(8, "p_current_wave", StringType(), required=False),
    NestedField(9, "p_degree", LongType(), required=False),
    NestedField(10, "p_probability", DoubleType(), required=False),
    NestedField(11, "p_confidence", DoubleType(), required=False),
    NestedField(12, "p_invalidation", DoubleType(), required=False),
    NestedField(13, "p_targets", StringType(), required=False),
    NestedField(14, "p_pivots", StringType(), required=False),
    NestedField(15, "p_rationale", StringType(), required=False),
    NestedField(29, "p_nesting_score", DoubleType(), required=False),
    NestedField(30, "p_forward", StringType(), required=False),
    # secondary count
    NestedField(16, "s_structure", StringType(), required=False),
    NestedField(17, "s_direction", StringType(), required=False),
    NestedField(18, "s_current_wave", StringType(), required=False),
    NestedField(19, "s_probability", DoubleType(), required=False),
    NestedField(20, "s_confidence", DoubleType(), required=False),
    NestedField(21, "s_invalidation", DoubleType(), required=False),
    NestedField(22, "s_targets", StringType(), required=False),
    NestedField(23, "s_rationale", StringType(), required=False),
    # bookkeeping
    NestedField(24, "uncertainty", DoubleType(), required=False),
    NestedField(25, "engine_ver", StringType(), required=True),
    NestedField(26, "git_sha", StringType(), required=False),
    NestedField(27, "computed_at", TimestamptzType(), required=False),
    identifier_field_ids=[1, 2, 3, 25],
)

# Small, append-mostly table (universe x ~4 intervals x 1/day). Month-only
# partition + symbol-clustered sort = ~1 file/month, no compaction — the same
# rationale as schwab_universe. Add bucket(symbol) only if it ever grows large.
ELLIOTT_WAVE_LABELS_PARTITION = PartitionSpec(
    PartitionField(source_id=2, field_id=1000, transform=MonthTransform(), name="date_month"),
)

ELLIOTT_WAVE_LABELS_SORT = SortOrder(
    SortField(source_id=1, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=3, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=2, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
)

# pyarrow schema for the sink — field order/types mirror the Iceberg schema.
ELLIOTT_WAVE_LABELS_ARROW = pa.schema([
    pa.field("symbol", pa.string(), nullable=False),
    pa.field("as_of_date", pa.date32(), nullable=False),
    pa.field("interval", pa.string(), nullable=False),
    pa.field("as_of_ts", pa.timestamp("us", tz="UTC"), nullable=True),
    pa.field("asset_class", pa.string(), nullable=True),
    pa.field("as_of_price", pa.float64(), nullable=True),
    pa.field("p_structure", pa.string(), nullable=True),
    pa.field("p_direction", pa.string(), nullable=True),
    pa.field("p_current_wave", pa.string(), nullable=True),
    pa.field("p_degree", pa.int64(), nullable=True),
    pa.field("p_probability", pa.float64(), nullable=True),
    pa.field("p_confidence", pa.float64(), nullable=True),
    pa.field("p_invalidation", pa.float64(), nullable=True),
    pa.field("p_targets", pa.string(), nullable=True),
    pa.field("p_pivots", pa.string(), nullable=True),
    pa.field("p_rationale", pa.string(), nullable=True),
    pa.field("p_nesting_score", pa.float64(), nullable=True),
    pa.field("p_forward", pa.string(), nullable=True),
    pa.field("s_structure", pa.string(), nullable=True),
    pa.field("s_direction", pa.string(), nullable=True),
    pa.field("s_current_wave", pa.string(), nullable=True),
    pa.field("s_probability", pa.float64(), nullable=True),
    pa.field("s_confidence", pa.float64(), nullable=True),
    pa.field("s_invalidation", pa.float64(), nullable=True),
    pa.field("s_targets", pa.string(), nullable=True),
    pa.field("s_rationale", pa.string(), nullable=True),
    pa.field("uncertainty", pa.float64(), nullable=True),
    pa.field("engine_ver", pa.string(), nullable=False),
    pa.field("git_sha", pa.string(), nullable=True),
    pa.field("computed_at", pa.timestamp("us", tz="UTC"), nullable=True),
])


def asset_class_for(symbol: str) -> str:
    """`/`-prefixed roots are futures; everything else is an equity."""
    return "future" if symbol.startswith("/") else "equity"


def glue_database(asset_class: str) -> str:
    return (settings.iceberg_futures_glue_database if asset_class == "future"
            else settings.iceberg_equities_glue_database)


def label_table_id(asset_class: str) -> str:
    return f"{glue_database(asset_class)}.{TABLE_NAME}"


def label_table_location(asset_class: str) -> str:
    return (f"s3://{settings.stock_lake_bucket}/{settings.iceberg_warehouse_prefix}/"
            f"{glue_database(asset_class)}/{TABLE_NAME}")


def _as_utc(dt: datetime) -> datetime:
    return dt.astimezone(timezone.utc) if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _candidate_cols(c: Optional[WaveCandidate], prefix: str, *, full: bool) -> dict:
    """Flatten a candidate to its column dict (primary carries pivots, the
    secondary omits them to keep the row lean)."""
    if c is None:
        return {}
    out = {
        f"{prefix}_structure": c.structure,
        f"{prefix}_direction": c.direction,
        f"{prefix}_current_wave": c.current_wave,
        f"{prefix}_probability": c.probability,
        f"{prefix}_confidence": c.confidence,
        f"{prefix}_invalidation": c.invalidation_price,
        f"{prefix}_targets": json.dumps(c.fib_targets),
        f"{prefix}_rationale": c.rationale,
    }
    if full:
        out[f"{prefix}_degree"] = c.degree
        out[f"{prefix}_pivots"] = json.dumps([p.model_dump(mode="json") for p in c.pivots])
        out[f"{prefix}_nesting_score"] = c.nesting_score
        out[f"{prefix}_forward"] = json.dumps(c.forward)
    return out


def labeling_to_row(lab: WaveLabeling, *, git_sha: str = "",
                    computed_at: Optional[datetime] = None) -> dict:
    """Map a WaveLabeling to one `elliott_wave_labels` row dict."""
    computed_at = _as_utc(computed_at or datetime.now(timezone.utc)).replace(microsecond=0)
    row: dict = {
        "symbol": lab.symbol,
        "as_of_date": _as_utc(lab.as_of).date(),
        "interval": lab.interval,
        "as_of_ts": _as_utc(lab.as_of),
        "asset_class": asset_class_for(lab.symbol),
        "as_of_price": lab.as_of_price,
        "uncertainty": lab.uncertainty,
        "engine_ver": lab.engine_ver,
        "git_sha": git_sha,
        "computed_at": computed_at,
    }
    # Ensure every nullable column is present (None) so the arrow frame is dense.
    for name in ELLIOTT_WAVE_LABELS_ARROW.names:
        row.setdefault(name, None)
    row.update(_candidate_cols(lab.primary, "p", full=True))
    row.update(_candidate_cols(lab.secondary, "s", full=False))
    return row
