"""
Market overview — index / futures tape for the dashboard banner.

The endpoint is a thin adapter over `QuoteService`:
  - QuoteService owns the chunking + invalidSymbols accumulation
    (replaces the old `_fetch_quotes_merged` helper that used to live
    here).
  - QuoteService also surfaces the provider name (Schwab fallback
    aware) so the response can include `provider:` for the dashboard.
  - This module keeps the banner-specific extraction (label,
    description, asset_type, net_change/change_pct from regular- vs
    quote-block fallbacks) because those fields are richer than the
    canonical `Quote` shape MCP tools will consume.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, Query

from app.api.schemas.market import MarketBannerResponse
from app.config import settings
from app.db import watchlist_repo
from app.services.readers.quote_service import QuoteService

logger = logging.getLogger(__name__)


def get_quote_service() -> QuoteService:
    """FastAPI dependency provider — override in tests."""
    return QuoteService.from_settings()


router = APIRouter()


def _split_symbols(raw: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for part in (raw or "").split(","):
        s = watchlist_repo.normalize_member_symbol(part)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
    return out


def _short_label(symbol: str, description: str) -> str:
    if symbol.startswith("$"):
        return symbol[1:] if len(symbol) > 1 else symbol
    if symbol.startswith("/"):
        return symbol[1:] if len(symbol) > 1 else symbol
    if description and len(description) <= 24:
        return description
    return symbol


def _extract_row(symbol: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    if not isinstance(payload, dict):
        return None
    q = payload.get("quote") or {}
    ref = payload.get("reference") or {}
    regular = payload.get("regular") or {}
    asset = (payload.get("assetMainType") or ref.get("assetType") or "").upper()
    # Schwab: lastPrice/mark are primary; some sessions only populate regular.*.
    last = q.get("lastPrice")
    if last is None:
        last = q.get("mark")
    if last is None:
        last = regular.get("regularMarketLastPrice")
    close = q.get("closePrice") or q.get("referencePrice") or ref.get("closePrice")
    net_chg = q.get("netChange")
    if net_chg is None:
        net_chg = regular.get("regularMarketNetChange")
    if net_chg is None and last is not None and close not in (None, 0):
        try:
            net_chg = float(last) - float(close)
        except (TypeError, ValueError):
            net_chg = None
    pct = q.get("netPercentChange")
    if pct is None:
        pct = regular.get("regularMarketPercentChange")
    if pct is None and net_chg is not None and close not in (None, 0):
        try:
            pct = (float(net_chg) / float(close)) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            pct = None
    desc = (ref.get("description") or payload.get("description") or "").strip()
    return {
        "symbol": symbol,
        "label": _short_label(symbol, desc),
        "description": desc,
        "asset_type": asset or None,
        "last": float(last) if last is not None else None,
        "net_change": float(net_chg) if net_chg is not None else None,
        "change_pct": float(pct) if pct is not None else None,
        "close": float(close) if close is not None else None,
    }


@router.get("/market/banner", response_model=MarketBannerResponse)
async def market_banner(
    symbols: Optional[str] = Query(
        None,
        description="Override comma-separated symbols (else settings.market_banner_symbols)",
    ),
    quote_service: QuoteService = Depends(get_quote_service),
) -> dict:
    """
    Index / futures tape for the dashboard banner. Returns one item
    per requested symbol with last/net_change/change_pct/close and a
    short display label.

    Response shape preserved verbatim for the dashboard:
      {as_of, provider, items: [...], errors: [...]}
    """
    want = _split_symbols(symbols) if symbols else _split_symbols(settings.market_banner_symbols)
    provider_name = quote_service.provider_name or None

    if not want:
        return _empty_response(provider_name, errors=[])

    if not _provider_supports_get_quotes(quote_service):
        return _empty_response(
            provider_name,
            errors=[{"message": "provider has no get_quotes"}],
        )

    try:
        raw, invalid = await quote_service.get_raw_quotes(want)
    except Exception as exc:  # noqa: BLE001 — boundary; preserve original behavior
        logger.warning("market_banner get_quotes failed: %s", exc)
        return _empty_response(provider_name, errors=[{"message": str(exc)}])

    if not raw and want:
        return _empty_response(
            provider_name,
            errors=[
                {"message": "empty quotes response (token expired, network, or unsupported batch)"},
            ],
        )

    errors: list[dict[str, Any]] = [
        {"symbol": sym, "message": "invalid or unsupported symbol"}
        for sym in invalid
    ]

    # Index by top-level key and by nested `symbol` (defensive for API variants).
    by_inner: dict[str, dict[str, Any]] = {}
    for k, v in raw.items():
        if not isinstance(v, dict):
            continue
        by_inner[str(k).upper()] = v
        inner_sym = v.get("symbol")
        if inner_sym:
            by_inner[str(inner_sym).upper()] = v

    items: list[dict[str, Any]] = []
    for sym in want:
        block = raw.get(sym) or by_inner.get(sym.upper())
        if not isinstance(block, dict):
            continue
        row = _extract_row(sym, block)
        if row and row.get("last") is not None:
            items.append(row)

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "provider": provider_name,
        "items": items,
        "errors": errors,
    }


def _provider_supports_get_quotes(svc: QuoteService) -> bool:
    """True iff the wrapped provider has a callable `get_quotes`."""
    return callable(getattr(svc._provider, "get_quotes", None))  # noqa: SLF001


def _empty_response(provider_name: Optional[str], *, errors: list[dict[str, Any]]) -> dict:
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "provider": provider_name,
        "items": [],
        "errors": errors,
    }
