"""
Top Movers API — wraps Schwab `/marketdata/v1/movers/{symbol_id}`.

Designed so an LLM/MCP tool can hit `GET /api/movers` with a tiny set of
self-describing parameters and get back a normalized list that doesn't
depend on Schwab's exact field naming (which varies across doc revisions).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services.watchlist_service import watchlist_service

logger = logging.getLogger(__name__)

router = APIRouter()

# Per Schwab Market Data API doc (api_docs/market_data_api.md §Movers).
VALID_INDEXES = {
    "$DJI", "$COMPX", "$SPX",
    "NYSE", "NASDAQ", "OTCBB",
    "INDEX_ALL", "EQUITY_ALL",
    "OPTION_ALL", "OPTION_PUT", "OPTION_CALL",
}
VALID_SORTS = {"VOLUME", "TRADES", "PERCENT_CHANGE_UP", "PERCENT_CHANGE_DOWN"}
VALID_FREQUENCIES = {0, 1, 5, 10, 30, 60}

# Friendly aliases so the LLM (and humans) don't have to memorize the `$` syntax.
INDEX_ALIASES = {
    "SPX": "$SPX", "SP500": "$SPX", "SP_500": "$SPX",
    "DJI": "$DJI", "DOW": "$DJI",
    "COMPX": "$COMPX", "NASDAQ_COMPOSITE": "$COMPX",
}


def _normalize_index(raw: str) -> str:
    s = (raw or "").strip().upper()
    s = INDEX_ALIASES.get(s, s)
    if s not in VALID_INDEXES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"invalid index '{raw}'. Allowed: "
                + ", ".join(sorted(VALID_INDEXES))
            ),
        )
    return s


def _pick(d: dict, *keys: str) -> Any:
    """Return the first non-None value from `d` matching any of `keys`."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return None


def _normalize_mover(row: dict) -> dict:
    """
    Schwab has shipped multiple shapes for the movers screener
    (`change`/`netChange`, `last`/`lastPrice`, `totalVolume`/`volume`, …).
    Map any of them to a single stable schema.
    """
    pct = _pick(row, "netPercentChange", "percentChange", "percent_change")
    # Schwab returns netPercentChange as a fraction in some docs (0.0108) and
    # as a percent number in others (1.08). Treat very small magnitudes as fractions.
    if isinstance(pct, (int, float)) and abs(pct) < 1:
        pct = pct * 100.0

    return {
        "symbol": _pick(row, "symbol"),
        "description": _pick(row, "description", "name"),
        "last": _pick(row, "lastPrice", "last"),
        "change": _pick(row, "netChange", "change"),
        "percent_change": pct,
        "direction": _pick(row, "direction"),
        "volume": _pick(row, "volume"),
        "total_volume": _pick(row, "totalVolume", "total_volume"),
        "trades": _pick(row, "trades"),
        "market_share": _pick(row, "marketShare", "market_share"),
    }


@router.get("/movers")
async def get_movers(
    index: str = Query(
        "$SPX",
        description="Index symbol. One of $DJI, $COMPX, $SPX, NYSE, NASDAQ, OTCBB, "
                    "INDEX_ALL, EQUITY_ALL, OPTION_ALL, OPTION_PUT, OPTION_CALL.",
    ),
    sort: str = Query(
        "PERCENT_CHANGE_UP",
        description="VOLUME | TRADES | PERCENT_CHANGE_UP | PERCENT_CHANGE_DOWN",
    ),
    frequency: int = Query(
        0,
        description="Lookback window in minutes (0 = since open). Allowed: 0,1,5,10,30,60.",
    ),
    limit: int = Query(10, ge=1, le=50, description="Truncate the returned list."),
):
    """
    Return the top movers for an index/exchange via Schwab Market Data API.

    The response is stable regardless of which Schwab field-naming variant
    is returned upstream, so it is safe to consume from the dashboard or an MCP tool.
    """
    idx = _normalize_index(index)
    sort_u = sort.strip().upper()
    if sort_u not in VALID_SORTS:
        raise HTTPException(
            status_code=400,
            detail=f"invalid sort '{sort}'. Allowed: " + ", ".join(sorted(VALID_SORTS)),
        )
    if frequency not in VALID_FREQUENCIES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid frequency '{frequency}'. Allowed: "
                   + ", ".join(str(f) for f in sorted(VALID_FREQUENCIES)),
        )

    provider = watchlist_service._ensure_provider()
    if provider is None or not hasattr(provider, "get_movers"):
        raise HTTPException(
            status_code=503,
            detail="Movers requires the Schwab provider. "
                   "Set DATA_PROVIDER=schwab and configure SCHWAB_* credentials.",
        )

    try:
        raw = await provider.get_movers(idx, sort=sort_u, frequency=frequency)
    except Exception as e:
        logger.error("get_movers failed: %s", e, exc_info=True)
        raise HTTPException(status_code=502, detail=f"Schwab movers call failed: {e}")

    screeners = (raw or {}).get("screeners") or []
    movers = [_normalize_mover(r) for r in screeners if isinstance(r, dict)]

    # Schwab's `sort` param is unreliable: it returns the top-N by magnitude of
    # move regardless of direction, so "PERCENT_CHANGE_UP" can include losers.
    # Filter & re-sort here so the response actually matches `sort`.
    def _num(x: Any) -> float:
        try:
            return float(x) if x is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    upstream_count = len(movers)
    if sort_u == "PERCENT_CHANGE_UP":
        movers = [m for m in movers if _num(m.get("percent_change")) > 0]
        movers.sort(key=lambda m: _num(m.get("percent_change")), reverse=True)
    elif sort_u == "PERCENT_CHANGE_DOWN":
        movers = [m for m in movers if _num(m.get("percent_change")) < 0]
        movers.sort(key=lambda m: _num(m.get("percent_change")))
    elif sort_u == "VOLUME":
        movers.sort(key=lambda m: _num(m.get("volume") or m.get("total_volume")), reverse=True)
    elif sort_u == "TRADES":
        movers.sort(key=lambda m: _num(m.get("trades")), reverse=True)

    movers = movers[:limit]

    return {
        "index": idx,
        "sort": sort_u,
        "frequency": frequency,
        "count": len(movers),
        "upstream_count": upstream_count,
        "filtered_out": max(0, upstream_count - len(movers)),
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "movers": movers,
    }
