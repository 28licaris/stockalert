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
from pyiceberg.transforms import IdentityTransform, YearTransform
from pyiceberg.types import (
    DateType,
    DoubleType,
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
    "split",            # forward stock split (factor > 1) or reverse (factor < 1)
    "cash_dividend",    # regular or special cash dividend
    "stock_dividend",   # stock dividend ("dividend" paid in shares)
    "spinoff",          # spin-off distribution (recorded as cash-equivalent if convertible)
]


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
