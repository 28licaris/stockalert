"""
Market overview — index / futures tape for the dashboard banner.

Implemented against **Schwab** `GET /marketdata/v1/quotes` via
`SchwabProvider.get_quotes` (symbol-keyed JSON). Symbols come from
`settings.market_banner_symbols` (comma-separated). Requests are batched in
chunks so one bad symbol or URL limits do not drop the whole strip.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Query

from app.config import get_market_quotes_provider, settings
from app.db import watchlist_repo

logger = logging.getLogger(__name__)

# Schwab accepts long comma-separated `symbols=` lists, but very large batches
# can fail closed; chunking keeps the tape resilient.
_QUOTE_CHUNK_SIZE = 25


async def _fetch_quotes_merged(getter, symbols: list[str]) -> dict[str, Any]:
    """Call `get_quotes` in chunks and merge symbol blocks + invalidSymbols."""
    merged: dict[str, Any] = {}
    invalid_acc: list[str] = []
    for i in range(0, len(symbols), _QUOTE_CHUNK_SIZE):
        chunk = symbols[i : i + _QUOTE_CHUNK_SIZE]
        try:
            part = await getter(chunk)
        except Exception as e:
            logger.warning("market_banner chunk get_quotes failed: %s", e)
            continue
        if not isinstance(part, dict):
            continue
        err = part.get("errors")
        if isinstance(err, dict):
            invalid_acc.extend(err.get("invalidSymbols") or [])
        for k, v in part.items():
            if k == "errors":
                continue
            if isinstance(v, dict):
                merged[k] = v
    if invalid_acc:
        merged["errors"] = {"invalidSymbols": invalid_acc}
    return merged


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


@router.get("/market/banner")
async def market_banner(
    symbols: Optional[str] = Query(
        None,
        description="Override comma-separated symbols (else settings.market_banner_symbols)",
    ),
) -> dict:
    want = _split_symbols(symbols) if symbols else _split_symbols(settings.market_banner_symbols)
    provider = get_market_quotes_provider()
    # Surface which provider actually backed this response so the dashboard
    # (and humans staring at the JSON) can confirm the configured DATA_PROVIDER
    # is the one serving the tape. ``provider_class`` is more robust than
    # ``DATA_PROVIDER`` because it survives the Schwab fallback inside
    # ``get_market_quotes_provider`` when the primary lacks ``get_quotes``.
    provider_class = type(provider).__name__ if provider is not None else None
    provider_name = (provider_class or "").replace("Provider", "").lower() or None

    if not want:
        return {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "provider": provider_name,
            "items": [],
            "errors": [],
        }

    getter = getattr(provider, "get_quotes", None)
    if getter is None:
        return {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "provider": provider_name,
            "items": [],
            "errors": [{"message": "provider has no get_quotes"}],
        }

    try:
        raw = await _fetch_quotes_merged(getter, want)
    except Exception as e:
        logger.warning("market_banner get_quotes failed: %s", e)
        return {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "provider": provider_name,
            "items": [],
            "errors": [{"message": str(e)}],
        }

    if raw == {} and want:
        errors = [{"message": "empty quotes response (token expired, network, or unsupported batch)"}]
        return {
            "as_of": datetime.now(timezone.utc).isoformat(),
            "provider": provider_name,
            "items": [],
            "errors": errors,
        }

    items: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if isinstance(raw, dict):
        err = raw.get("errors")
        if isinstance(err, dict):
            inv = err.get("invalidSymbols") or []
            for sym in inv:
                errors.append({"symbol": sym, "message": "invalid or unsupported symbol"})
        # Index by top-level key and by nested `symbol` (defensive for API variants).
        by_inner: dict[str, dict[str, Any]] = {}
        for k, v in raw.items():
            if k == "errors" or not isinstance(v, dict):
                continue
            by_inner[str(k).upper()] = v
            inner_sym = v.get("symbol")
            if inner_sym:
                by_inner[str(inner_sym).upper()] = v
        for sym in want:
            block = raw.get(sym) or by_inner.get(sym.upper())
            if not isinstance(block, dict):
                continue
            row = _extract_row(sym, block)
            if row and row.get("last") is not None:
                items.append(row)
    elif isinstance(raw, list):
        # Rare list-shaped quote payloads: [{ "symbol": "SPY", ... }, ...]
        by_sym: dict[str, dict[str, Any]] = {}
        for entry in raw:
            if isinstance(entry, dict) and entry.get("symbol"):
                by_sym[str(entry["symbol"]).upper()] = entry
        for sym in want:
            block = by_sym.get(sym.upper())
            if isinstance(block, dict):
                row = _extract_row(sym, block)
                if row and row.get("last") is not None:
                    items.append(row)
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "provider": provider_name,
        "items": items,
        "errors": errors,
    }
