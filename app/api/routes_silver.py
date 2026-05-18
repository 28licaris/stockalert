"""
Silver-tier HTTP routes — canonical OHLCV + bar-quality readers.

Two endpoints:

  GET /api/silver/bars/{symbol}          — 1-minute OHLCV from silver
  GET /api/silver/bar-quality/{symbol}   — per-(symbol, date) quality audit

Both read `silver.ohlcv_1m` / `silver.bar_quality` — the canonical,
provider-merged, corp-action-adjusted consumer surfaces. Per the
[silver_layer_plan consumer contract](../../../docs/silver_layer_plan.md),
every reader (chart, screener, indicator, backtest, MCP tool) reads
silver — never bronze directly.

The same `SilverOhlcvReader` backs the MCP tools in
`app/mcp/tools/silver_ohlcv.py` — one service, two surfaces, identical
Pydantic shapes.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.services.readers.schemas import (
    BarQualityResponse,
    SilverBarsResponse,
)
from app.services.readers.silver_ohlcv_reader import SilverOhlcvReader

logger = logging.getLogger(__name__)

router = APIRouter()


@lru_cache(maxsize=1)
def _build_reader() -> SilverOhlcvReader:
    return SilverOhlcvReader.from_settings()


def get_silver_ohlcv_reader() -> SilverOhlcvReader:
    """FastAPI dependency provider — override in tests."""
    return _build_reader()


@router.get(
    "/silver/bars/{symbol}",
    response_model=SilverBarsResponse,
)
def get_silver_bars(
    symbol: str = Path(..., min_length=1, description="Ticker (case-insensitive)."),
    start: datetime = Query(
        ...,
        description=(
            "Lower bound on `timestamp` (inclusive). UTC. Use an "
            "ISO-8601 timestamp with TZ, e.g. '2024-06-10T13:30:00Z'."
        ),
    ),
    end: datetime = Query(
        ...,
        description=(
            "Upper bound on `timestamp` (exclusive). UTC. Half-open "
            "[start, end) interval mirrors slicing semantics."
        ),
    ),
    reader: SilverOhlcvReader = Depends(get_silver_ohlcv_reader),
) -> SilverBarsResponse:
    """Return 1-minute silver bars for `symbol` in `[start, end)`.

    Reads `silver.ohlcv_1m` — the canonical, provider-merged,
    corp-action-adjusted view. Every row carries BOTH `_raw` and
    `_adj` columns; consumers choose which to read.

    Snapshot-pinned: the `snapshot_id` in the response lets callers
    replay against the same lake state for deterministic results.

    Returns empty `bars` if:
      - silver.ohlcv_1m hasn't been built yet (cold start), or
      - no bars match the window.
    """
    start_utc = _coerce_utc(start)
    end_utc = _coerce_utc(end)
    if start_utc >= end_utc:
        raise HTTPException(
            status_code=400,
            detail=f"start ({start_utc}) must be < end ({end_utc}).",
        )

    return reader.get_bars(symbol, start_utc, end_utc)


@router.get(
    "/silver/bar-quality/{symbol}",
    response_model=BarQualityResponse,
)
def get_silver_bar_quality(
    symbol: str = Path(..., min_length=1, description="Ticker (case-insensitive)."),
    since: Optional[date] = Query(
        None,
        description="Lower bound on `date` (inclusive). Omit for all history.",
    ),
    until: Optional[date] = Query(
        None,
        description="Upper bound on `date` (inclusive). Omit for through-today.",
    ),
    reader: SilverOhlcvReader = Depends(get_silver_ohlcv_reader),
) -> BarQualityResponse:
    """Return per-(symbol, date) bar-quality audit rows.

    Reads `silver.bar_quality` — the audit ledger silver_ohlcv_build
    produces alongside silver.ohlcv_1m. Surface for operators inspecting
    nightly-build health and agents asking "any silent gaps in my
    training set?".
    """
    if since is not None and until is not None and since > until:
        raise HTTPException(
            status_code=400,
            detail=f"since ({since}) must be <= until ({until}).",
        )

    return reader.get_bar_quality(symbol, since=since, until=until)


def _coerce_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
