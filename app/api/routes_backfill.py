"""HTTP API for historical-data backfill (quick + deep)."""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.ingest.backfill_service import backfill_service

logger = logging.getLogger(__name__)

router = APIRouter()


class BackfillRequest(BaseModel):
    symbols: List[str] = Field(..., description="Symbols to backfill, e.g. ['SPY', 'AAPL']")
    days: int = Field(
        30, ge=1, le=7500,
        description=(
            "Lookback window in days. Max 7500 (~20.5y) — Schwab's "
            "/pricehistory practical ceiling for daily bars on long-listed "
            "symbols. For symbols listed less than 20y ago, Schwab will "
            "just return data from listing date forward."
        ),
    )
    force: bool = Field(
        False,
        description=(
            "Bypass the per-symbol throttle. Use for explicit user-triggered "
            "buttons; leave `false` for auto-enqueued/background calls."
        ),
    )


@router.post("/backfill")
async def backfill_quick(req: BackfillRequest):
    """
    Enqueue a QUICK (latency-first) backfill for each symbol.

    Short-circuits if the database already covers >=90% of the requested window,
    or if the throttle hasn't elapsed (unless `force=true`).
    """
    if not req.symbols:
        raise HTTPException(400, "symbols list is empty")
    results = [backfill_service.enqueue_quick(s, days=req.days, force=req.force) for s in req.symbols]
    return {"kind": "quick", "days": req.days, "jobs": results}


@router.post("/backfill/deep")
async def backfill_deep(req: BackfillRequest):
    """
    Enqueue a DEEP (completeness-first) backfill of 1-min bars. Gap-aware:
    only fetches the portion of `[now - days, now]` that the DB does not
    already cover, chunked into ~9-day windows (Schwab pricehistory 1-min
    limit is ~48 days, so requests beyond that return empty).

    Concurrency 1 globally. Throttled to once-per-symbol-per-week unless
    `force=true`.
    """
    if not req.symbols:
        raise HTTPException(400, "symbols list is empty")
    days = req.days if req.days else 365
    results = [backfill_service.enqueue_deep(s, days=days, force=req.force) for s in req.symbols]
    return {"kind": "deep", "days": days, "jobs": results}


# NOTE: the former /backfill/daily and /backfill/intraday endpoints were
# removed — all chart timeframes (5m/15m/30m/1h/1d) are now resampled on read
# from ohlcv_1m, so the ohlcv_5m / ohlcv_daily tables (and their backfills) no
# longer exist. See docs/architecture_v2/02_schema.md.


class GapFillRequest(BaseModel):
    symbols: List[str]
    days: int | None = Field(default=30, ge=1, le=365)
    source: str = Field(
        default="ohlcv_1m",
        description="Source table to scan for gaps. Only ohlcv_1m.",
    )
    force: bool = Field(False, description="Bypass the per-symbol gap-fill throttle")


@router.post("/backfill/gaps")
async def backfill_gaps(req: GapFillRequest):
    """
    Detect within-session gaps in the specified source table for each symbol
    over `[now - days, now]` and re-fetch ONLY the gap ranges from the
    provider. Unlike `/backfill`, this does NOT short-circuit on overall
    coverage ratio - it targets the actual holes inside the window.

    Use this when:
    - The chart shows "N within-session gaps (M bars missing)" badges.
    - You just brought a streamer back up after a disconnect.
    - The coverage sweeper detects partial-day holes.
    """
    if not req.symbols:
        raise HTTPException(400, "symbols list is empty")
    if req.source != "ohlcv_1m":
        raise HTTPException(400, f"invalid source {req.source!r}; must be ohlcv_1m")
    days = req.days if req.days else 30
    results = [
        backfill_service.enqueue_gap_fill(s, days=days, source=req.source, force=req.force)
        for s in req.symbols
    ]
    return {"kind": "gap_fill", "days": days, "source": req.source, "jobs": results}


@router.get("/backfill/gaps")
async def list_gaps(
    symbol: str,
    days: int = 30,
    source: str = "ohlcv_1m",
    max_results: int = 100,
):
    """
    Return the current within-session gaps for a symbol without enqueueing
    a fill. Useful for the UI to show "X gaps remain" after a fill, and for
    the (future) coverage sweeper to inspect coverage.
    """
    if source != "ohlcv_1m":
        raise HTTPException(400, f"invalid source {source!r}; must be ohlcv_1m")
    from datetime import datetime, timezone, timedelta
    from app.db import queries
    now = datetime.now(timezone.utc)
    start = now - timedelta(days=days)
    gaps = await queries.find_intraday_gaps_async(
        symbol, start, now, source_table=source, max_results=max_results,
    )
    return {
        "symbol": symbol.upper(),
        "source": source,
        "window_days": days,
        "gap_count": len(gaps),
        "missing_bars_total": sum(g["missing"] for g in gaps),
        "gaps": [
            {
                "prev_ts": g["prev_ts"].isoformat(),
                "next_ts": g["next_ts"].isoformat(),
                "missing": g["missing"],
            }
            for g in gaps
        ],
    }


@router.get("/backfill/coverage")
async def backfill_coverage(
    symbol: str = Query(..., description="Single symbol, e.g. SPY"),
    days: int = Query(30, ge=1, le=7500),
):
    """Report DB coverage of `symbol` over the last `days` days (no fetch)."""
    sym = (symbol or "").strip().upper()
    if not sym:
        raise HTTPException(400, "symbol is required")
    try:
        return await backfill_service.coverage(sym, days=days)
    except Exception as e:
        logger.error("Coverage query failed for %s: %s", sym, e, exc_info=True)
        raise HTTPException(500, str(e))


@router.get("/backfill/status")
async def backfill_status(symbol: Optional[str] = Query(None)):
    """
    Return live job state. With no `symbol`, returns the map for every symbol
    the service has seen since startup. With a symbol, returns just that one.
    """
    if symbol:
        return backfill_service.status(symbol)
    return backfill_service.status()
