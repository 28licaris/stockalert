"""
MCP tools — instrument / symbol lookup + autocomplete.

Wraps the Schwab `/instruments` endpoint (re-ranked + deduped per
`schwab_provider.search_instruments`). Agents use these to resolve
fuzzy queries ('apple' → AAPL) before calling the bar / quote tools
that expect canonical tickers.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.config import get_provider
from app.mcp.middleware import tool_call
from app.mcp.server import mcp

logger = logging.getLogger(__name__)


@mcp.tool()
async def search_instrument(query: str, limit: int = 10) -> list[dict[str, Any]]:
    """Fuzzy ticker search by symbol prefix OR company name.

    USE WHEN: an agent has a user-typed string and needs to resolve
    it to canonical tickers — 'apple' → AAPL, 'nvd' → NVDA. Combines
    symbol-prefix matching with company-name search, then re-ranks
    so the most likely intended match floats to the top.

    Args:
        query: User text. Symbol prefixes ('nvd'), full names
            ('apple'), and free-form descriptions ('semiconductor
            stocks') all accepted. Empty string returns [].
        limit: Max results to return (default 10). Re-ranking happens
            before truncation so the top results are quality.

    Returns:
        List of `{symbol, description, asset_type, exchange, cusip}`
        dicts. Empty list on no match or provider error (degraded mode).

    Cost: 100-500ms (one or two Schwab HTTP calls under the hood).
    Don't loop this — for known tickers use `get_instrument` instead.
    """
    with tool_call("search_instrument", query=query, limit=limit):
        provider = get_provider()
        searcher = getattr(provider, "search_instruments", None)
        if searcher is None:
            return []
        try:
            return await searcher(query, limit=limit)
        except Exception as exc:  # noqa: BLE001
            logger.warning("search_instrument failed: %s", exc)
            return []


@mcp.tool()
async def get_instruments(
    symbols: list[str],
    projection: str = "symbol-search",
) -> dict[str, Any]:
    """Detailed metadata for a list of known tickers.

    USE WHEN: an agent has canonical symbols and wants the metadata —
    full company name, asset type ('EQUITY', 'ETF', 'MUTUAL_FUND',
    'OPTION', 'FUTURE', 'INDEX'), exchange code, CUSIP. For fuzzy
    string queries use `search_instrument` instead.

    Args:
        symbols: List of tickers (exact match).
        projection: Schwab projection field — controls how much
            metadata comes back. Options:
            - 'symbol-search' (default, fast; symbol + description)
            - 'fundamental' (slower; adds market cap, sector, etc.)
            - 'symbol-regex' (treats `symbols[0]` as a regex)
            - 'desc-search' / 'desc-regex' (search by description)

    Returns:
        Schwab response dict, typically
        `{"instruments": [{"symbol": "AAPL", "description": ..., ...}, ...]}`.
        Empty dict on provider error.

    Errors: Returns `{}` rather than raising on provider failure
    (degraded mode).
    """
    with tool_call("get_instruments", symbol_count=len(symbols), projection=projection):
        provider = get_provider()
        getter = getattr(provider, "get_instruments", None)
        if getter is None:
            return {}
        try:
            return await getter(symbols, projection=projection)
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_instruments failed: %s", exc)
            return {}
