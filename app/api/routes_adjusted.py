"""
Adjusted OHLCV HTTP route — the canonical v2 reader-side surface.

  GET /api/v1/adjusted/bars/{symbol}   — 1-minute split-adjusted OHLCV

Reads `equities.polygon_adjusted` via `AdjustedOhlcvReader`. Every row
carries `adj_factor` (CV1's Gate 2) so consumers needing raw prices
can recover them client-side: `raw = adj * adj_factor`.

The same reader backs the MCP tools in
`app/mcp/tools/adjusted_ohlcv.py` — one service, two surfaces,
identical Pydantic shapes.

The v1 `/api/silver/bars/{symbol}` URL was retired in CV20; the
`/api/silver/bar-quality/{symbol}` endpoint had no v2 backing table
and was deleted entirely (data-integrity invariants are now enforced
by the Spark adjustment job + corp-actions ingest, not a downstream
audit table).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, Path, Query

from app.services.readers.adjusted_ohlcv_reader import AdjustedOhlcvReader
from app.services.readers.schemas import SilverBarsResponse

logger = logging.getLogger(__name__)

router = APIRouter()


@lru_cache(maxsize=1)
def _build_reader() -> AdjustedOhlcvReader:
    return AdjustedOhlcvReader.from_settings()


def get_adjusted_ohlcv_reader() -> AdjustedOhlcvReader:
    """FastAPI dependency provider — override in tests."""
    return _build_reader()


@router.get(
    "/adjusted/bars/{symbol}",
    response_model=SilverBarsResponse,
)
def get_adjusted_bars(
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
    reader: AdjustedOhlcvReader = Depends(get_adjusted_ohlcv_reader),
) -> SilverBarsResponse:
    """Return 1-minute split-adjusted bars for `symbol` in `[start, end)`.

    Reads `equities.polygon_adjusted` — built whole-market weekly by
    the Spark `polygon_adjustment_job`. Each row carries the cumulative
    future-splits factor as `adj_factor`; multiply back to recover raw.

    Snapshot-pinned: the `snapshot_id` in the response lets callers
    replay against the same lake state for deterministic results.

    Returns empty `bars` if:
      - equities.polygon_adjusted hasn't been populated yet (cold
        start before the first whole-market Spark run), or
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


def _coerce_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
