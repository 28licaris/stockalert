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

import asyncio
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

    # ClickHouse is the PRIMARY source: the hot 1m tier already holds the
    # exact last price and the prior regular-session close needed for the
    # change %. This serves the default ETF tape in ~10–50ms versus the
    # ~650ms live-quote REST round-trip the provider needs.
    ch_rows = await _ch_banner(want)
    missing = [s for s in want if s not in ch_rows]

    errors: list[dict[str, Any]] = []
    provider_rows: dict[str, dict[str, Any]] = {}
    used_provider = False

    # Provider fallback ONLY for symbols CH doesn't carry — indices ($SPX),
    # futures roots, or anything outside the ingested universe.
    if missing and _provider_supports_get_quotes(quote_service):
        try:
            raw, invalid = await quote_service.get_raw_quotes(missing)
            used_provider = True
            by_inner: dict[str, dict[str, Any]] = {}
            for k, v in (raw or {}).items():
                if not isinstance(v, dict):
                    continue
                by_inner[str(k).upper()] = v
                inner_sym = v.get("symbol")
                if inner_sym:
                    by_inner[str(inner_sym).upper()] = v
            for sym in missing:
                block = (raw or {}).get(sym) or by_inner.get(sym.upper())
                if not isinstance(block, dict):
                    continue
                row = _extract_row(sym, block)
                if row and row.get("last") is not None:
                    provider_rows[sym] = row
            errors.extend(
                {"symbol": sym, "message": "invalid or unsupported symbol"}
                for sym in invalid
            )
        except Exception as exc:  # noqa: BLE001 — boundary
            logger.warning("market_banner provider fallback failed: %s", exc)
            errors.append({"message": f"provider fallback failed: {exc}"})

    # Anything still unresolved is surfaced explicitly — never silently dropped.
    for sym in want:
        if sym not in ch_rows and sym not in provider_rows:
            if not any(e.get("symbol") == sym for e in errors):
                errors.append({"symbol": sym, "message": "no data available"})

    items = [ch_rows.get(s) or provider_rows.get(s) for s in want]
    items = [it for it in items if it]

    if ch_rows and used_provider:
        provider_label = f"clickhouse+{provider_name}" if provider_name else "clickhouse"
    elif ch_rows:
        provider_label = "clickhouse"
    else:
        provider_label = provider_name

    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "provider": provider_label,
        "items": items,
        "errors": errors,
    }


def _provider_supports_get_quotes(svc: QuoteService) -> bool:
    """True iff the wrapped provider has a callable `get_quotes`."""
    return callable(getattr(svc._provider, "get_quotes", None))  # noqa: SLF001


# Friendly tape labels for the configured banner ETFs (CH has no company
# names). Falls back to the raw ticker for anything not listed.
_BANNER_LABELS: dict[str, str] = {
    "SPY": "S&P 500",
    "QQQ": "Nasdaq 100",
    "IWM": "Russell 2000",
    "DIA": "Dow Jones",
    "GLD": "Gold",
    "TLT": "20Y Treasuries",
    "VIXY": "VIX Futures",
    "SLV": "Silver",
}

# The two most recent regular-session (≤16:00 ET) daily closes per symbol.
# We anchor "previous close" to the DATA's own sessions, not the wall clock:
# overnight/pre-open, `now()`'s ET date has no session yet, so comparing
# against "yesterday" would pick the same day as the latest price. Taking the
# last two RTH session closes (LIMIT 2 BY symbol) and using the OLDER one as
# `prev` gives the correct day-over-day change in every session state.
_LAST2_RTH_CLOSE_SQL = """
SELECT symbol, d, close FROM (
  SELECT
    symbol,
    toDate(toTimeZone(timestamp, 'America/New_York')) AS d,
    argMax(close, timestamp) AS close
  FROM stocks.ohlcv_1m
  WHERE symbol IN ('{inlist}')
    AND timestamp >= now() - INTERVAL 14 DAY
    AND (toHour(toTimeZone(timestamp, 'America/New_York')) * 60
         + toMinute(toTimeZone(timestamp, 'America/New_York'))) <= 960
  GROUP BY symbol, d
  ORDER BY symbol, d DESC
  LIMIT 2 BY symbol
)
"""


async def _ch_banner(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Build banner rows from ClickHouse — the primary, fast path.

    Per symbol: `last` = latest 1m close (current price, any session);
    `close` = prior regular-session close; `net_change`/`change_pct`
    derived from the two. Returns a dict keyed by symbol for only the
    symbols CH actually has (callers fall back to the provider for the
    rest). Any CH error degrades to an empty dict — never raises into the
    request path.
    """
    from app.db.client import get_client

    safe = [s for s in symbols if s and "'" not in s]
    if not safe:
        return {}
    inlist = "','".join(safe)
    try:
        client = get_client()
        # Sequential, not concurrent: the shared CH client is single-session
        # and rejects concurrent queries on one connection. Each query is a
        # ~10ms indexed scan, so two in series is still well under the
        # provider round-trip we're replacing.
        last_res = await asyncio.to_thread(
            client.query,
            f"SELECT symbol, argMax(close, timestamp) AS last"
            f" FROM stocks.ohlcv_1m WHERE symbol IN ('{inlist}')"
            f" AND timestamp >= now() - INTERVAL 14 DAY GROUP BY symbol",
        )
        rth_res = await asyncio.to_thread(
            client.query, _LAST2_RTH_CLOSE_SQL.format(inlist=inlist)
        )
    except Exception as exc:  # noqa: BLE001 — CH unavailable degrades to provider
        logger.warning("ch banner query failed: %s", exc)
        return {}

    last = {r[0]: float(r[1]) for r in last_res.result_rows if r[1] is not None}
    # rth rows are (symbol, day, close) ordered day-DESC per symbol; the second
    # row per symbol is the prior session's close.
    rth_by_sym: dict[str, list[float]] = {}
    for sym, _day, close in rth_res.result_rows:
        if close is not None:
            rth_by_sym.setdefault(sym, []).append(float(close))
    prev = {sym: rows[1] for sym, rows in rth_by_sym.items() if len(rows) >= 2}

    out: dict[str, dict[str, Any]] = {}
    for sym, last_px in last.items():
        prev_px = prev.get(sym)
        net = (last_px - prev_px) if prev_px is not None else None
        pct = (net / prev_px * 100.0) if (net is not None and prev_px) else None
        label = _BANNER_LABELS.get(sym, sym)
        out[sym] = {
            "symbol": sym,
            "label": label,
            "description": label,
            "asset_type": "ETF",
            "last": last_px,
            "net_change": net,
            "change_pct": pct,
            "close": prev_px,
        }
    return out


def _empty_response(provider_name: Optional[str], *, errors: list[dict[str, Any]]) -> dict:
    return {
        "as_of": datetime.now(timezone.utc).isoformat(),
        "provider": provider_name,
        "items": [],
        "errors": errors,
    }
