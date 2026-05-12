"""HTTP API for historical-data backfill (quick + deep)."""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.services.backfill_service import backfill_service

logger = logging.getLogger(__name__)

router = APIRouter()


class BackfillRequest(BaseModel):
    symbols: List[str] = Field(..., description="Symbols to backfill, e.g. ['SPY', 'AAPL']")
    days: int = Field(30, ge=1, le=2000, description="Lookback window in days")


@router.post("/backfill")
async def backfill_quick(req: BackfillRequest):
    """
    Enqueue a QUICK (latency-first) backfill for each symbol.

    Short-circuits if the database already covers >=90% of the requested window.
    Concurrency 3 globally.
    """
    if not req.symbols:
        raise HTTPException(400, "symbols list is empty")
    results = [backfill_service.enqueue_quick(s, days=req.days) for s in req.symbols]
    return {"kind": "quick", "days": req.days, "jobs": results}


@router.post("/backfill/deep")
async def backfill_deep(req: BackfillRequest):
    """
    Enqueue a DEEP (completeness-first) backfill of 1-min bars. Gap-aware:
    only fetches the portion of `[now - days, now]` that the DB does not
    already cover, chunked into ~9-day windows (Schwab pricehistory 1-min
    limit is ~48 days, so requests beyond that return empty).

    Concurrency 1 globally.
    """
    if not req.symbols:
        raise HTTPException(400, "symbols list is empty")
    days = req.days if req.days else 365
    results = [backfill_service.enqueue_deep(s, days=days) for s in req.symbols]
    return {"kind": "deep", "days": days, "jobs": results}


@router.post("/backfill/daily")
async def backfill_daily(req: BackfillRequest):
    """
    Enqueue a DAILY backfill (native daily candles from the provider, stored
    in `ohlcv_daily`). Schwab serves 20+ years of daily history per call so
    this is fast even for long windows.
    """
    if not req.symbols:
        raise HTTPException(400, "symbols list is empty")
    days = req.days if req.days else 365 * 2
    results = [backfill_service.enqueue_daily(s, days=days) for s in req.symbols]
    return {"kind": "daily", "days": days, "jobs": results}


@router.post("/backfill/intraday")
async def backfill_intraday(req: BackfillRequest):
    """
    Enqueue an INTRADAY backfill: 5-minute candles, stored in `ohlcv_5m`.
    Schwab caps 1-min bars at ~48 days; this populates ~270 days of
    medium-resolution history so 5m/15m/30m/1h/4h charts can stretch
    further back than 48 days.
    """
    if not req.symbols:
        raise HTTPException(400, "symbols list is empty")
    days = req.days if req.days else 270
    results = [backfill_service.enqueue_intraday(s, days=days) for s in req.symbols]
    return {"kind": "intraday", "days": days, "jobs": results}


@router.get("/backfill/coverage")
async def backfill_coverage(
    symbol: str = Query(..., description="Single symbol, e.g. SPY"),
    days: int = Query(30, ge=1, le=2000),
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
