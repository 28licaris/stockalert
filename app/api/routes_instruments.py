"""
Instrument autocomplete API — wraps the provider's `search_instruments`.

This is the "type a few letters, get a dropdown of matching tickers" route
used by the dashboard's add-to-watchlist input and (eventually) a global
symbol lookup bar.

Designed provider-agnostically:
  - The provider returns `{symbol, description, exchange, asset_type}` records.
  - This route is a thin pass-through with cache + validation.

A tiny in-memory TTL cache fronts the provider so a user typing 'N', 'NV',
'NVD' doesn't fan out three full Schwab calls (each one being subject to OAuth
overhead) — duplicate prefixes within `CACHE_TTL_S` reuse the previous result.
"""
from __future__ import annotations

import logging
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.services.live.watchlist_service import watchlist_service

logger = logging.getLogger(__name__)

router = APIRouter()

# Small TTL so live ticker descriptions stay reasonably fresh while still
# absorbing the keystroke burst of an autocomplete field. Keyed by
# (lowercased_query, limit).
CACHE_TTL_S = 60.0
_cache: dict[tuple[str, int], tuple[float, list[dict]]] = {}
_CACHE_MAX_ENTRIES = 512


def _cache_get(key: tuple[str, int]) -> Optional[list[dict]]:
    entry = _cache.get(key)
    if entry is None:
        return None
    ts, val = entry
    if time.monotonic() - ts > CACHE_TTL_S:
        _cache.pop(key, None)
        return None
    return val


def _cache_put(key: tuple[str, int], val: list[dict]) -> None:
    if len(_cache) >= _CACHE_MAX_ENTRIES:
        # Drop the oldest ~10% of entries to keep memory bounded; cheap because
        # this is invoked at human typing speed, not in a hot loop.
        drop = sorted(_cache.items(), key=lambda kv: kv[1][0])[: _CACHE_MAX_ENTRIES // 10]
        for k, _ in drop:
            _cache.pop(k, None)
    _cache[key] = (time.monotonic(), val)


@router.get("/instruments/search")
async def search_instruments(
    q: str = Query(..., min_length=1, max_length=32, description="Ticker prefix or company name fragment"),
    limit: int = Query(10, ge=1, le=25, description="Max suggestions to return"),
):
    """
    Autocomplete endpoint. Returns up to `limit` matching instruments.

    Response shape:
      {
        "query": "NVD",
        "results": [
          {"symbol": "NVDA", "description": "NVIDIA Corp",
           "exchange": "NASDAQ", "asset_type": "EQUITY"},
          ...
        ],
        "cached": true
      }
    """
    query = q.strip()
    if not query:
        raise HTTPException(400, "query is empty")

    key = (query.lower(), limit)
    cached = _cache_get(key)
    if cached is not None:
        return {"query": query, "results": cached, "cached": True}

    # Get the live provider from the watchlist service so we don't reconstruct
    # OAuth state per call. Falls back to an empty list if the provider isn't
    # ready (e.g. token expired) — caller treats this as "no suggestions".
    provider = getattr(watchlist_service, "_provider", None)
    if provider is None:
        logger.debug("search_instruments: provider not ready, returning empty list")
        return {"query": query, "results": [], "cached": False}

    try:
        results = await provider.search_instruments(query, limit=limit)
    except Exception as e:
        # Don't propagate provider failures to the UI — autocomplete failure
        # should never block the user from typing a ticker manually.
        logger.warning("search_instruments(%r) provider error: %s", query, e)
        results = []

    _cache_put(key, results)
    return {"query": query, "results": results, "cached": False}
