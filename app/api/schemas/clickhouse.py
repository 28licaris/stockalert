"""
ClickHouse ad-hoc query response schemas. Backs `/api/v1/clickhouse/*`.

Two endpoints feed the cockpit's CH query page (FE-CONTRACTS-6a):

  - `GET /api/v1/clickhouse/schema` — list tables + columns for the
    schema sidebar's tab-complete and click-to-insert flow. Hides
    `system.*` tables to keep internal metadata off the cockpit.

  - `POST /api/v1/clickhouse/query` — execute read-only SQL. Server
    enforces row cap + 30s timeout + ClickHouse's `readonly=1` setting
    (DML / DDL hard-rejected by CH itself, not by string parsing).

The "developer's cadillac" goal: full SQL power with safety rails the
operator cannot accidentally circumvent.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────────────
# Schema browser
# ─────────────────────────────────────────────────────────────────────


class CHColumn(BaseModel):
    """One column on a CH table."""

    name: str
    type: str = Field(
        ...,
        description=(
            "ClickHouse type string (e.g. 'UInt64', 'String', "
            "'DateTime64(3)', 'LowCardinality(String)'). Kept as the "
            "raw upstream string — cockpit doesn't normalize."
        ),
    )


class CHTable(BaseModel):
    """One table in the schema browser."""

    database: str
    name: str
    engine: str = Field(
        default="",
        description="MergeTree, ReplacingMergeTree, etc. Empty for views.",
    )
    row_count: Optional[int] = Field(
        default=None,
        description=(
            "Cheap row estimate from `system.tables.total_rows`. Null "
            "when CH didn't propagate a count (typical for views and "
            "freshly-created tables)."
        ),
    )
    columns: list[CHColumn]


class ClickHouseSchemaResponse(BaseModel):
    """Full schema listing. Cached server-side for ~60s."""

    tables: list[CHTable]
    cached: bool = Field(
        ...,
        description="True if served from the in-process cache rather than a fresh CH round-trip.",
    )


# ─────────────────────────────────────────────────────────────────────
# Query execution
# ─────────────────────────────────────────────────────────────────────


class ClickHouseQueryRequest(BaseModel):
    """Body for `POST /api/v1/clickhouse/query`.

    `max_rows` and `timeout_seconds` cap the operator's request from the
    cockpit side. The server clamps them against its own hard ceiling
    so a malicious / typo'd value can't blow them out.
    """

    sql: str = Field(
        ...,
        min_length=1,
        max_length=20_000,
        description=(
            "The SQL to execute. Trailing semicolon is stripped. CH "
            "applies `readonly=1` so DDL / DML / SETTINGS attempts are "
            "rejected by the engine itself."
        ),
    )
    max_rows: int = Field(
        default=1000,
        ge=1,
        le=30_000,
        description="Per-query cap on returned rows (server clamps to ≤30k).",
    )
    timeout_seconds: int = Field(
        default=30,
        ge=1,
        le=120,
        description="Per-query execution timeout in seconds (server clamps to ≤120).",
    )


class ClickHouseQueryResponse(BaseModel):
    """Result of a single query execution."""

    columns: list[CHColumn] = Field(
        ...,
        description="Column metadata in the order returned by the engine.",
    )
    rows: list[list] = Field(
        ...,
        description=(
            "Row data as a 2-D list. Cell values are JSON-safe — "
            "datetimes are ISO strings, nulls are JSON null. The "
            "cockpit renders each cell with its column type for "
            "alignment / formatting hints."
        ),
    )
    row_count: int = Field(
        ..., description="Number of rows in `rows`. Equal to len(rows)."
    )
    truncated: bool = Field(
        ...,
        description="True iff the query produced more rows than `max_rows` and was server-side truncated.",
    )
    duration_ms: float = Field(
        ...,
        description="Query execution time in milliseconds (wall clock, server-measured).",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "columns": [
                    {"name": "symbol", "type": "LowCardinality(String)"},
                    {"name": "row_count", "type": "UInt64"},
                ],
                "rows": [
                    ["AAPL", 1583],
                    ["NVDA", 1582],
                ],
                "row_count": 2,
                "truncated": False,
                "duration_ms": 12.4,
            }
        }
    )
