"""
MCP tool — recent news for a symbol (or market-wide).

Backed by the same CH `news_items` store as the cockpit feed: official-record
items (SEC EDGAR filings; govt releases later), AI-summarized with a link to the
source document. Lets an agent answer "any news on NVDA?" without leaving the
platform. See docs/news_alerts_spec.md.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.mcp.middleware import tool_call
from app.mcp.server import mcp

logger = logging.getLogger(__name__)


@mcp.tool()
async def get_news(
    symbols: Optional[str] = None,
    types: Optional[str] = None,
    limit: int = 25,
) -> dict[str, Any]:
    """Recent official-record news items, newest first.

    USE WHEN: an agent needs filings/news context — "any news on AAPL?",
    "recent 8-Ks for my watchlist", "insider buys today".

    Args:
        symbols: Comma-separated tickers (e.g. 'AAPL,NVDA'). Omit for
          market-wide. Market-wide (macro) items are always included even
          when a symbol filter is set.
        types: Comma-separated event types (e.g. '8-K,4'). Omit for all.
        limit: Max items (1–500; default 25).

    Returns:
        `{"items": [ {symbol, event_type, title, summary, why_it_matters,
        materiality, sentiment, url, published_at, enriched}, ... ]}`.
        Items link to the source document; we never republish the body.

    Errors: returns `{"items": []}` in degraded mode rather than raising.
    """
    with tool_call("get_news", symbols=symbols, types=types, limit=limit):
        from app.services.news.reader import read_news

        def _csv(v: Optional[str]) -> Optional[list[str]]:
            if not v:
                return None
            out = [t.strip() for t in v.split(",") if t.strip()]
            return out or None

        try:
            items = read_news(
                symbols=_csv(symbols), event_types=_csv(types), limit=limit
            )
        except Exception:  # noqa: BLE001 — degrade, don't surface a raw exception
            logger.exception("get_news failed (symbols=%s types=%s)", symbols, types)
            return {"items": []}
        return {"items": [i.model_dump(mode="json") for i in items]}


@mcp.tool()
async def get_economic_data(series_id: Optional[str] = None) -> dict[str, Any]:
    """Latest US economic indicators (CPI, jobs, unemployment), with history.

    USE WHEN: an agent needs macro context for a trade/thesis — "what's the
    latest CPI?", "is the labor market cooling?", "recent inflation trend".

    Args:
        series_id: BLS series id for history (e.g. 'CUUR0000SA0' CPI,
          'LNS14000000' unemployment, 'CES0000000001' nonfarm payrolls).
          Omit to get the latest figure for every tracked indicator.

    Returns:
        Without series_id: `{"indicators": [{name, value, value_label, change,
        period_label, unit}, ...]}`. With series_id: `{"series_id": ...,
        "history": [{period_label, value}, ...]}` (newest first).

    Errors: returns the empty shape in degraded mode rather than raising.
    """
    with tool_call("get_economic_data", series_id=series_id):
        from app.services.news.econ import EconService

        try:
            svc = EconService.from_settings()
            if series_id:
                hist = svc.history(series_id)
                return {"series_id": series_id, "history": [h.model_dump(mode="json") for h in hist]}
            return {"indicators": [i.model_dump(mode="json") for i in svc.latest()]}
        except Exception:  # noqa: BLE001 — degrade
            logger.exception("get_economic_data failed (series_id=%s)", series_id)
            return {"indicators": []} if not series_id else {"series_id": series_id, "history": []}
