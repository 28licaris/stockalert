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
from app.services.readers.schemas import (
    CrossProviderDiffResponse,
    SilverBarsResponse,
    SymbolCoverageResponse,
)

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
    include_live: bool = Query(
        False,
        description=(
            "When true, UNION equities.polygon_adjusted with "
            "equities.schwab_universe (the live + tip-fill source) so "
            "the response covers polygon's adjusted history AND today's "
            "live bars in one call. Use this for charts/queries whose "
            "window extends past polygon_adjusted's latest weekly "
            "Spark snapshot (typically <7 days stale). Polygon wins "
            "duplicates on (symbol, timestamp)."
        ),
    ),
    reader: AdjustedOhlcvReader = Depends(get_adjusted_ohlcv_reader),
) -> SilverBarsResponse:
    """Return 1-minute split-adjusted bars for `symbol` in `[start, end)`.

    Default (`include_live=false`) reads `equities.polygon_adjusted`
    only — the canonical adjusted store, built whole-market weekly by
    the Spark `polygon_adjustment_job`. Each row carries the cumulative
    future-splits factor as `adj_factor`; multiply back to recover raw.

    With `include_live=true` the response also includes rows from
    `equities.schwab_universe` (live + tip-fill, also pre-adjusted with
    `adj_factor=1.0`). Polygon rows win duplicates on
    (symbol, timestamp); the result is the smooth deep-history-to-today
    series charts and ML training sets actually want.

    Snapshot-pinned: the `snapshot_id` in the response lets callers
    replay against the same lake state for deterministic results. When
    `include_live=true`, snapshot_id reflects the polygon side (the
    canonical adjusted source).

    Returns empty `bars` if:
      - equities.polygon_adjusted hasn't been populated yet (cold
        start before the first whole-market Spark run) AND
        `include_live=false`, or
      - no bars match the window.
    """
    start_utc = _coerce_utc(start)
    end_utc = _coerce_utc(end)
    if start_utc >= end_utc:
        raise HTTPException(
            status_code=400,
            detail=f"start ({start_utc}) must be < end ({end_utc}).",
        )

    if include_live:
        return reader.get_bars_union(symbol, start_utc, end_utc)
    return reader.get_bars(symbol, start_utc, end_utc)


@router.get(
    "/adjusted/symbols/{symbol}/coverage",
    response_model=SymbolCoverageResponse,
)
def get_symbol_coverage(
    symbol: str = Path(..., min_length=1, description="Ticker (case-insensitive)."),
    reader: AdjustedOhlcvReader = Depends(get_adjusted_ohlcv_reader),
) -> SymbolCoverageResponse:
    """Coverage stats for `symbol` across both v2 adjusted sources.

    Returns per-table row counts, earliest/latest timestamps, and
    pinned snapshot IDs for:
      - `equities.polygon_adjusted` — deep adjusted history (weekly Spark)
      - `equities.schwab_universe`  — live + tip-fill (continuous)

    Use this to answer:
      - "Is NVDA ready to chart? How far back?" → polygon_adjusted.earliest_timestamp
      - "How current is the data?" → schwab_universe.latest_timestamp
      - "Cold-start before first Spark run?" → polygon_adjusted.row_count == 0

    Either source independently degrades to row_count=0 / None
    timestamps when its table is cold-start empty or its scan fails
    — the endpoint never 500s on transient lake issues; it surfaces
    them as empty coverage so the consumer can decide what to do.

    Cheap: two metadata-only scans (timestamp column projection +
    bucket pruning on symbol). Sub-second on warm cache.
    """
    return reader.get_symbol_coverage(symbol)


@router.get(
    "/adjusted/symbols/{symbol}/diff",
    response_model=CrossProviderDiffResponse,
)
def get_cross_provider_diff(
    symbol: str = Path(..., min_length=1, description="Ticker (case-insensitive)."),
    start: datetime = Query(
        ...,
        description="Window start (inclusive), UTC ISO-8601.",
    ),
    end: datetime = Query(
        ...,
        description="Window end (exclusive), UTC ISO-8601.",
    ),
    tolerance: float = Query(
        0.005,
        ge=0.0,
        le=1.0,
        description=(
            "Surface rows where abs(pct_diff) > tolerance. "
            "Default 0.005 (50bps) filters sub-cent rounding while "
            "catching real corp-action / data-correction divergences."
        ),
    ),
    reader: AdjustedOhlcvReader = Depends(get_adjusted_ohlcv_reader),
) -> CrossProviderDiffResponse:
    """Surface close-price disagreements between
    `equities.polygon_adjusted` and `equities.schwab_universe` for
    `symbol` in `[start, end)`.

    Inner-joins on (symbol, timestamp); single-sided rows are NOT
    surfaced (use /coverage for that question). `compared_count` is
    the denominator — `count` is the disagreement numerator.

    `pct_diff = (polygon.close - schwab.close) / polygon.close` —
    polygon is the canonical adjusted source; sign convention is
    polygon-minus-schwab so callers can compare against directional
    tolerances.

    Use this to answer:
      - Did a corp-action correction land in polygon but not schwab?
      - Is a strategy's bad day a data bug or a real signal?
      - Should I trust today's close for this symbol?
    """
    start_utc = _coerce_utc(start)
    end_utc = _coerce_utc(end)
    if start_utc >= end_utc:
        raise HTTPException(
            status_code=400,
            detail=f"start ({start_utc}) must be < end ({end_utc}).",
        )
    return reader.get_cross_provider_diff(
        symbol, start_utc, end_utc, tolerance=tolerance,
    )


def _coerce_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt
