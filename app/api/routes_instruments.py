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

import asyncio
import logging
import re
import time
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.api.schemas.instruments import (
    InstrumentLookupResponse,
    InstrumentMatch,
    InstrumentSearchResponse,
)
from app.services.stream import stream_service

logger = logging.getLogger(__name__)

router = APIRouter()

_SAFE_SYMBOL_RE = re.compile(r"[^A-Z0-9$/.]")


async def _ch_symbol_fallback(query: str, limit: int) -> list[dict]:
    """Search known symbols in ClickHouse when the provider is unavailable.

    Matches symbol prefix (case-insensitive) against the live ohlcv_1m table.
    Returns symbols with empty description — autocomplete still works, just
    without company name enrichment.
    """
    from app.db.client import get_client
    safe = _SAFE_SYMBOL_RE.sub("", query.upper())
    if not safe:
        return []
    try:
        client = get_client()
        result = await asyncio.to_thread(
            client.query,
            "SELECT DISTINCT symbol FROM stocks.ohlcv_1m"
            f" WHERE symbol LIKE '{safe}%'"
            f" ORDER BY symbol LIMIT {limit}",
        )
        return [
            {"symbol": row[0], "description": "", "exchange": "", "asset_type": "EQUITY"}
            for row in result.result_rows
        ]
    except Exception as exc:
        logger.warning("ch symbol fallback failed: %s", exc)
        return []

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


@router.get("/instruments/search", response_model=InstrumentSearchResponse)
async def search_instruments(
    q: str = Query(..., min_length=1, max_length=32, description="Ticker prefix or company name fragment"),
    limit: int = Query(10, ge=1, le=25, description="Max suggestions to return"),
) -> InstrumentSearchResponse:
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
        return InstrumentSearchResponse(
            query=query,
            results=[InstrumentMatch(**r) for r in cached],
            cached=True,
        )

    # Get the live provider from the stream service so we don't reconstruct
    # OAuth state per call. Falls back to an empty list if the provider isn't
    # ready (e.g. token expired) — caller treats this as "no suggestions".
    provider = stream_service.get_provider()
    if provider is None:
        logger.debug("search_instruments: provider not ready, using CH fallback")
        results = await _ch_symbol_fallback(query, limit)
        return InstrumentSearchResponse(query=query, results=results, cached=False)

    try:
        results = await provider.search_instruments(query, limit=limit)
    except Exception as e:
        logger.warning("search_instruments(%r) provider error: %s", query, e)
        results = []

    if not results:
        results = await _ch_symbol_fallback(query, limit)

    _cache_put(key, results)
    return InstrumentSearchResponse(
        query=query,
        results=[InstrumentMatch(**r) for r in results],
        cached=False,
    )


def _lookup_cache_key(symbol: str) -> tuple[str, int]:
    """Cache key for a single-symbol lookup. Shared TTL with the search
    cache so a prefix-match warmup also serves the lookup case."""
    return (symbol.lower(), 1)


def _missing_match(symbol: str) -> dict:
    """Synthetic entry for symbols the provider couldn't resolve. Clients
    detect this by `description == ""`."""
    return {"symbol": symbol, "description": "", "exchange": "", "asset_type": ""}


@router.get("/instruments/lookup", response_model=InstrumentLookupResponse)
async def lookup_instruments(
    symbols: str = Query(
        ...,
        description=(
            "Comma-separated symbols to resolve. Order is preserved in "
            "the response. Unknown symbols come back with an empty "
            "`description` rather than being dropped."
        ),
    ),
):
    """Batch metadata lookup for a known set of symbols.

    Used by the cockpit to enrich Stream Service / watchlist rows with
    company descriptions in one round-trip.

    Strategy:
      1. Check the in-memory per-symbol cache (60s TTL, shared with the
         autocomplete route so prefix-typed -> list-render flows are warm).
      2. For uncached symbols, issue a SINGLE Schwab batch /instruments
         call with `projection=symbol-search`. Previously this loop did
         one HTTP round-trip per symbol — ~450ms each, ~46s for 103
         symbols cold. The batch call is one round-trip.
      3. Stitch cache hits + fresh results back into the requested order.
    """
    raw = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not raw:
        raise HTTPException(400, "symbols list is empty")
    if len(raw) > 500:
        raise HTTPException(400, "too many symbols (max 500 per call)")

    provider = stream_service.get_provider()

    # Pass 1: collect cache hits; compile the list of symbols that still
    # need an upstream call.
    cached_count = 0
    fresh: dict[str, dict] = {}  # symbol -> normalized instrument dict
    needed: list[str] = []
    for sym in raw:
        c = _cache_get(_lookup_cache_key(sym))
        if c is not None:
            hit = next((r for r in c if (r.get("symbol") or "").upper() == sym), None)
            if hit is not None:
                fresh[sym] = hit
                cached_count += 1
                continue
            # Cached miss (we asked Schwab once, it didn't know the symbol).
            # Treat as cached so we don't re-hammer Schwab.
            fresh[sym] = _missing_match(sym)
            cached_count += 1
            continue
        needed.append(sym)

    # Pass 2: ONE batch Schwab call for everything not in cache.
    if needed and provider is not None:
        try:
            data = await provider.get_instruments(needed, projection="symbol-search")
            normalize = getattr(provider, "_normalize_instrument", None)
            for it in (data or {}).get("instruments", []) or []:
                norm = normalize(it) if normalize else {
                    "symbol": (it.get("symbol") or "").upper(),
                    "description": it.get("description") or "",
                    "exchange": it.get("exchange") or "",
                    "asset_type": it.get("assetType") or it.get("type") or "",
                }
                sym = norm["symbol"]
                if sym:
                    fresh[sym] = norm
                    # Cache per symbol so future single-symbol lookups
                    # (e.g. autocomplete pick) hit the warm path.
                    _cache_put(_lookup_cache_key(sym), [norm])
            # Symbols Schwab didn't return -> cache the miss so we don't
            # re-call upstream for unknown tickers.
            for sym in needed:
                if sym not in fresh:
                    fresh[sym] = _missing_match(sym)
                    _cache_put(_lookup_cache_key(sym), [])
        except Exception as e:  # noqa: BLE001 — boundary
            logger.warning(
                "lookup_instruments batch (%d syms) provider error: %s",
                len(needed), e,
            )
            for sym in needed:
                fresh.setdefault(sym, _missing_match(sym))
    else:
        # Provider not ready — return placeholders for the needed set
        # without caching (so a future call after warm-up hits upstream).
        for sym in needed:
            fresh.setdefault(sym, _missing_match(sym))

    # Pass 3: emit results in the order the caller asked for.
    results: list[InstrumentMatch] = [
        InstrumentMatch(**fresh[sym]) for sym in raw
    ]
    return InstrumentLookupResponse(results=results, cached_count=cached_count)
