"""
MCP tool — market hours (equity / option session schedule).

Wraps Schwab's `/markets` endpoint. Agents use this to bound
queries to RTH ("only show me regular-hours bars"), to decide
whether a market is open before placing an order (future Trading
AI work), or to schedule training data windows around session
boundaries.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.config import get_provider
from app.mcp.middleware import tool_call
from app.mcp.server import mcp

logger = logging.getLogger(__name__)


@mcp.tool()
async def get_market_hours(market: Optional[str] = None) -> dict[str, Any]:
    """Market open/close schedule from Schwab.

    USE WHEN: an agent needs session-aware logic — "is the market
    open right now?", "what are today's regular session boundaries
    for SPY?", "did the NYSE close early today?"

    Args:
        market: Optional market filter — 'equity', 'option', 'future',
            'bond', 'forex'. Omit for all markets in one response.
            Schwab's default scope: today; multi-day windows aren't
            exposed by this endpoint (one day at a time).

    Returns:
        Schwab response dict — typically
        `{"equity": {"EQ": {"date": "2024-08-01", "marketType": "EQUITY",
                            "isOpen": true, "sessionHours": {...}}}}`.
        The `sessionHours` block carries `preMarket`, `regularMarket`,
        and `postMarket` arrays of `{"start", "end"}` pairs.
        Empty dict on provider error.
    """
    with tool_call("get_market_hours", market=market):
        provider = get_provider()
        getter = getattr(provider, "get_market_hours", None)
        if getter is None:
            return {}
        try:
            return await getter(market_id=market) if market else await getter()
        except Exception as exc:  # noqa: BLE001
            logger.warning("get_market_hours failed: %s", exc)
            return {}
