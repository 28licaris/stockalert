"""
Top Movers API — wraps Schwab `/marketdata/v1/movers/{symbol_id}`.

Designed so an LLM/MCP tool can hit `GET /api/movers` with a tiny set of
self-describing parameters and get back a normalized list that doesn't
depend on Schwab's exact field naming (which varies across doc revisions).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query

from app.services.live.watchlist_service import watchlist_service

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

# Pseudo-indexes expand to a set of real Schwab indexes that we call in parallel
# and merge. Each index is capped at 10 rows by Schwab, but the *pools differ*
# (e.g. $SPX returns mega-caps, $COMPX returns small-caps), so fan-out is the
# only way to get more than 10 unique candidates.
INDEX_FANOUT = {
    "ALL_US": ["$SPX", "$DJI", "$COMPX", "NYSE", "NASDAQ"],
    "ALL_LARGECAP": ["$SPX", "$DJI"],
    "ALL_EXCHANGES": ["NYSE", "NASDAQ", "OTCBB"],
}


def _resolve_indexes(raw: str) -> list[str]:
    """
    Accept a single index ($SPX), a friendly alias (DOW), a pseudo-index (ALL_US),
    or a comma-separated list (e.g. '$SPX,$DJI,$COMPX'). Returns a deduped list
    of real Schwab index symbols.
    """
    tokens = [t.strip().upper() for t in (raw or "").split(",") if t.strip()]
    if not tokens:
        raise HTTPException(status_code=400, detail="index is required")

    resolved: list[str] = []
    for tok in tokens:
        # Expand pseudo-indexes first.
        if tok in INDEX_FANOUT:
            for inner in INDEX_FANOUT[tok]:
                if inner not in resolved:
                    resolved.append(inner)
            continue
        canonical = INDEX_ALIASES.get(tok, tok)
        if canonical not in VALID_INDEXES:
            allowed = sorted(VALID_INDEXES) + sorted(INDEX_FANOUT.keys())
            raise HTTPException(
                status_code=400,
                detail=f"invalid index '{tok}'. Allowed: " + ", ".join(allowed),
            )
        if canonical not in resolved:
            resolved.append(canonical)
    return resolved


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
        description=(
            "Index symbol(s). One of: $DJI, $COMPX, $SPX, NYSE, NASDAQ, OTCBB, "
            "INDEX_ALL, EQUITY_ALL, OPTION_ALL, OPTION_PUT, OPTION_CALL. "
            "Or a comma-separated list ('$SPX,$DJI'), or a pseudo-index: "
            "ALL_US (fans out to $SPX,$DJI,$COMPX,NYSE,NASDAQ), "
            "ALL_LARGECAP ($SPX,$DJI), ALL_EXCHANGES (NYSE,NASDAQ,OTCBB). "
            "Schwab caps each individual index at 10 rows, so fan-out is the "
            "only way to get more than 10 unique movers."
        ),
    ),
    sort: str = Query(
        "PERCENT_CHANGE_UP",
        description="VOLUME | TRADES | PERCENT_CHANGE_UP | PERCENT_CHANGE_DOWN",
    ),
    frequency: int = Query(
        0,
        description="Lookback window in minutes (0 = since open). Allowed: 0,1,5,10,30,60.",
    ),
    limit: int = Query(10, ge=1, le=100, description="Truncate the returned list."),
):
    """
    Return the top movers for one or more indexes via Schwab Market Data API.

    Schwab's `/movers/{index}` endpoint hard-caps each index at 10 rows. We fan
    out across the resolved index list in parallel, dedupe by symbol, then
    filter+sort according to `sort`. The response is stable regardless of which
    Schwab field-naming variant the upstream returns.
    """
    indexes = _resolve_indexes(index)
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
            detail=(
                "The configured stream provider does not support a movers "
                "endpoint. Use DATA_PROVIDER=schwab or DATA_PROVIDER=polygon."
            ),
        )

    async def _fetch(one_idx: str) -> tuple[str, list[dict], str | None]:
        try:
            raw = await provider.get_movers(one_idx, sort=sort_u, frequency=frequency)
            rows = (raw or {}).get("screeners") or []
            return one_idx, [_normalize_mover(r) for r in rows if isinstance(r, dict)], None
        except Exception as e:
            logger.warning("get_movers(%s) failed: %s", one_idx, e)
            return one_idx, [], str(e)

    # Some providers (Polygon) return market-wide gainers/losers regardless of
    # the index argument, so a fan-out would just N-multiply the same call.
    # Honour ``provider.MOVERS_MARKET_WIDE`` to call exactly once in that case
    # and report the response under a synthetic ``ALL_US`` bucket so the
    # downstream payload doesn't lie about which indexes were actually queried.
    market_wide = bool(getattr(provider, "MOVERS_MARKET_WIDE", False))
    if market_wide:
        sym, rows, err = await _fetch(indexes[0])
        results = [("ALL_US", rows, err)]
        effective_indexes = ["ALL_US"]
    else:
        results = await asyncio.gather(*[_fetch(i) for i in indexes])
        effective_indexes = indexes

    # Dedup by symbol, but remember which index(es) each ticker came from.
    by_symbol: dict[str, dict] = {}
    per_index_counts: dict[str, int] = {}
    errors: dict[str, str] = {}
    for one_idx, rows, err in results:
        per_index_counts[one_idx] = len(rows)
        if err:
            errors[one_idx] = err
        for row in rows:
            sym = (row.get("symbol") or "").upper()
            if not sym:
                continue
            if sym not in by_symbol:
                row["source_indexes"] = [one_idx]
                by_symbol[sym] = row
            else:
                by_symbol[sym]["source_indexes"].append(one_idx)

    movers = list(by_symbol.values())
    upstream_count = len(movers)

    def _num(x: Any) -> float:
        try:
            return float(x) if x is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

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

    provider_class = type(provider).__name__
    provider_name = provider_class.replace("Provider", "").lower() or None

    return {
        "index": effective_indexes[0] if len(effective_indexes) == 1 else ",".join(effective_indexes),
        "indexes": effective_indexes,
        "provider": provider_name,
        "sort": sort_u,
        "frequency": frequency,
        "count": len(movers),
        "upstream_count": upstream_count,
        "filtered_out": max(0, upstream_count - len(movers)),
        "per_index_counts": per_index_counts,
        "errors": errors or None,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "movers": movers,
    }
