"""HTTP API for the live-bar ingestion watchlist."""
from __future__ import annotations

import logging
from typing import List

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.db import queries
from app.services.watchlist_service import watchlist_service

logger = logging.getLogger(__name__)

router = APIRouter()


def _ts(v):
    return v.isoformat() if hasattr(v, "isoformat") else str(v)


class SymbolsRequest(BaseModel):
    symbols: List[str] = Field(..., description="Stock symbols, e.g. ['SPY', 'AAPL']")


@router.get("/watchlist")
async def get_watchlist():
    """Return the current watchlist and stream status."""
    return watchlist_service.status()


@router.post("/watchlist/add")
async def add_to_watchlist(req: SymbolsRequest):
    """Add one or more symbols and immediately subscribe to their live bars."""
    try:
        return watchlist_service.add(req.symbols)
    except Exception as e:
        logger.error("Watchlist add failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/watchlist/remove")
async def remove_from_watchlist(req: SymbolsRequest):
    """Remove one or more symbols and unsubscribe from their live bars."""
    try:
        return watchlist_service.remove(req.symbols)
    except Exception as e:
        logger.error("Watchlist remove failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/watchlist/snapshot")
async def watchlist_snapshot():
    """Latest bar in ClickHouse for each watchlist symbol (drives the dashboard)."""
    symbols = watchlist_service.list_symbols()
    rows = await queries.latest_bar_per_symbol_async(symbols) if symbols else []
    by_symbol = {r["symbol"]: r for r in rows}
    snapshot = []
    for s in symbols:
        r = by_symbol.get(s)
        if r is None:
            snapshot.append({"symbol": s, "bar_count": 0, "ts": None})
        else:
            snapshot.append(
                {
                    "symbol": s,
                    "ts": _ts(r["ts"]),
                    "open": r["open"],
                    "high": r["high"],
                    "low": r["low"],
                    "close": r["close"],
                    "volume": r["volume"],
                    "bar_count": r["bar_count"],
                }
            )
    return snapshot
