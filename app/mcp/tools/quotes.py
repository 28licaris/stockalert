"""
MCP tools backed by `QuoteService` — current quotes via provider REST.

These hit Schwab (or whichever provider `get_market_quotes_provider`
resolves to, with the same fallback chain the banner uses) for current
prices / bid / ask / volume. Distinct from `tools/live.py` which reads
CH-stored 1-minute bars — quotes are an instant snapshot, bars are a
historical row.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from app.mcp.middleware import tool_call
from app.mcp.server import mcp
from app.services.readers.quote_service import QuoteService
from app.services.readers.schemas import Quote, QuotesResponse

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _svc() -> QuoteService:
    return QuoteService.from_settings()


@mcp.tool()
async def get_quote(symbol: str) -> Optional[Quote]:
    """Current quote for one symbol (last / bid / ask / open / volume).

    USE WHEN: an agent wants "what's AAPL trading at right now". For
    multiple symbols at once, prefer `get_quotes` (single chunked call).

    Args:
        symbol: Ticker.

    Returns:
        Quote with normalized fields, or `None` if the provider
        couldn't resolve the symbol (invalid ticker, transient error,
        rate-limited).

    Cost: 50-500ms typical (one provider HTTP call). Subject to
    provider rate limits — don't loop this; use `get_quotes` for
    batched requests.
    """
    with tool_call("get_quote", symbol=symbol):
        return await _svc().get_quote(symbol)


@mcp.tool()
async def get_quotes(symbols: list[str]) -> QuotesResponse:
    """Current quotes for many symbols in one call (chunked).

    USE WHEN: an agent needs prices across a watchlist or candidate
    universe. Chunks large batches under the hood (default 25/chunk)
    with per-chunk error isolation — one bad chunk doesn't drop the
    whole tape.

    Args:
        symbols: Up to a few hundred tickers.

    Returns:
        QuotesResponse with:
          - `quotes`: dict[symbol -> Quote] (only successfully-resolved
            symbols).
          - `invalid_symbols`: list[str] (provider couldn't resolve).
          - `count`: len(quotes).

    Cost: ~50-500ms per chunk of 25 symbols. 100 symbols = 4 sequential
    chunks = ~1-2s typical.
    """
    with tool_call("get_quotes", symbol_count=len(symbols)):
        return await _svc().get_quotes(symbols)
