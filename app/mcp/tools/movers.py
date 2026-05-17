"""
MCP tool — top movers (gainers / losers) from Schwab.

Schwab's `/movers/{symbol_id}` returns the day's biggest moves for a
named index ($SPX, $COMPX, $DJI, etc.). The return shape is provider-
specific; we pass it through as-is rather than forcing a normalized
schema because the dashboard already speaks this shape and forcing a
schema here would lose useful fields.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

from app.config import get_provider
from app.mcp.middleware import tool_call
from app.mcp.server import mcp

logger = logging.getLogger(__name__)


@mcp.tool()
async def get_movers(
    symbol_id: str = "$SPX",
    sort: Optional[str] = None,
    frequency: Optional[int] = None,
) -> dict[str, Any]:
    """Top movers for a market index from Schwab.

    USE WHEN: an agent is doing market-context scanning — "what's
    leading the SPX today", "where's volume / volatility concentrated
    in tech", "anything unusual in this morning's session."

    Args:
        symbol_id: Schwab market identifier. Common values:
          '$SPX' (S&P 500, default), '$COMPX' (NASDAQ Composite),
          '$DJI' (Dow Jones), 'NASDAQ', 'NYSE', 'OTCBB'.
        sort: Optional sort key per Schwab spec — 'VOLUME',
          'TRADES', 'PERCENT_CHANGE_UP', 'PERCENT_CHANGE_DOWN'.
          Omit for Schwab's default ordering.
        frequency: Optional minutes-window for the movers calc
          (per Schwab: 0/1/5/10/30/60). Omit for default.

    Returns:
        Raw Schwab response dict — typically
        `{"screeners": [{"symbol": "AAPL", "lastPrice": ..., ...}, ...]}`.
        Schema preserved as-is so dashboard parsing and agent
        parsing stay consistent.

    Errors: Any provider error returns an empty dict (degraded mode);
    Schwab token expiry surfaces as `{}` rather than an exception.
    """
    with tool_call("get_movers", symbol_id=symbol_id, sort=sort, frequency=frequency):
        provider = get_provider()
        getter = getattr(provider, "get_movers", None)
        if getter is None:
            logger.warning("get_movers: active provider has no get_movers method")
            return {}
        kwargs: dict = {}
        if sort is not None:
            kwargs["sort"] = sort
        if frequency is not None:
            kwargs["frequency"] = frequency
        try:
            result = await getter(symbol_id, **kwargs)
        except Exception as exc:  # noqa: BLE001 — boundary; degraded-mode contract
            logger.warning("get_movers failed: %s", exc)
            return {}
        return result if isinstance(result, dict) else {}
