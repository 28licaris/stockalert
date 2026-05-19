"""HTTP API for the cockpit's ad-hoc ClickHouse query page.

Two endpoints:
  - GET  /api/v1/clickhouse/schema   table + column listing (60s cache)
  - POST /api/v1/clickhouse/query    read-only query execution

Safety lives in `app/services/clickhouse_query/query_service.py` —
this module is a thin HTTP adapter that shapes the request + maps
CH errors into the typed ErrorResponse envelope.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException

from app.api.schemas.clickhouse import (
    CHColumn,
    CHTable,
    ClickHouseQueryRequest,
    ClickHouseQueryResponse,
    ClickHouseSchemaResponse,
)
from app.services.clickhouse_query import query_service

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/clickhouse/schema", response_model=ClickHouseSchemaResponse)
async def list_clickhouse_schema() -> ClickHouseSchemaResponse:
    """List user tables + columns for the cockpit's schema sidebar.

    Hides `system.*` and `INFORMATION_SCHEMA.*`. Cached server-side
    for ~60s.
    """
    try:
        tables_raw, cached = await asyncio.to_thread(query_service.list_schema)
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.error("ch schema listing failed: %s", exc, exc_info=True)
        raise HTTPException(500, f"ClickHouse schema listing failed: {exc}")

    tables = [
        CHTable(
            database=t["database"],
            name=t["name"],
            engine=t.get("engine", ""),
            row_count=t.get("row_count"),
            columns=[CHColumn(**c) for c in t.get("columns", [])],
        )
        for t in tables_raw
    ]
    return ClickHouseSchemaResponse(tables=tables, cached=cached)


@router.post("/clickhouse/query", response_model=ClickHouseQueryResponse)
async def execute_clickhouse_query(
    req: ClickHouseQueryRequest,
) -> ClickHouseQueryResponse:
    """Execute a single read-only SQL statement against ClickHouse.

    Safety rails are applied in the service layer:
      - CH `readonly=1` (DDL/DML rejected by the engine)
      - row cap (clamped to ≤30k, default 1000)
      - timeout (clamped to ≤120s, default 30s)
      - max_bytes_to_read = 1 GiB
      - max_memory_usage = 4 GiB

    CH errors (syntax error, readonly violation, timeout, etc.)
    surface as HTTP 400 with the typed ErrorResponse envelope so the
    cockpit can display them inline below the SQL editor.
    """
    try:
        result = await asyncio.to_thread(
            query_service.execute,
            req.sql,
            max_rows=req.max_rows,
            timeout_seconds=req.timeout_seconds,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as exc:  # noqa: BLE001 — boundary
        # CH error messages are informative (line number, expected token,
        # etc.) and the operator needs to see them verbatim.
        msg = str(exc)
        logger.debug("ch query error: %s", msg)
        raise HTTPException(400, f"ClickHouse rejected the query: {msg}")

    return ClickHouseQueryResponse(
        columns=[CHColumn(**c) for c in result["columns"]],
        rows=result["rows"],
        row_count=result["row_count"],
        truncated=result["truncated"],
        duration_ms=result["duration_ms"],
    )
