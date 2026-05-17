"""
MCP tools backed by `SignalReader` — divergence + future detectors.

These expose fired signals from the live monitor's CH `signals` table.
Agents use them to surface "what just fired" alerts and to backtest
against historical detector output.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import Optional

from app.mcp.middleware import tool_call
from app.mcp.server import mcp
from app.services.readers.schemas import SignalsResponse
from app.services.readers.signal_reader import SignalReader

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _reader() -> SignalReader:
    return SignalReader.from_settings()


@mcp.tool()
def get_recent_signals(limit: int = 50) -> SignalsResponse:
    """Most recent fired signals across all symbols, newest-first.

    USE WHEN: an agent wants to skim "what just fired" — divergence
    detections, future breakout/MA-crossover events, anything emitted
    by `app/signals/` detectors via the live monitor.

    Args:
        limit: How many signals to return. Default 50.

    Returns:
        SignalsResponse with signals=list[Signal]. Each Signal has the
        detector name (`signal_type`), the underlying indicator (`rsi`,
        `macd`, `tsi`), the timestamp + price + indicator value at fire,
        and the pivot timestamps (`p1_ts`, `p2_ts`).
    """
    with tool_call("get_recent_signals", limit=limit):
        signals = _reader().get_recent_signals(limit=limit)
        return SignalsResponse(symbol=None, signals=signals, count=len(signals))


@mcp.tool()
def get_signals_by_symbol(
    symbol: Optional[str] = None,
    limit: int = 100,
) -> SignalsResponse:
    """Signals filtered by symbol (or all symbols if symbol is None).

    USE WHEN: an agent is drilling into a specific name — "show me
    every divergence AAPL has fired this month" — or, with symbol=None,
    a larger sweep than `get_recent_signals` (different default limit).

    Args:
        symbol: Ticker filter, or None for all-symbol sweep.
        limit: How many signals to return. Default 100.

    Returns:
        SignalsResponse with signals=list[Signal]. The `symbol` field
        echoes the filter so consumers can record what was queried.
    """
    with tool_call("get_signals_by_symbol", symbol=symbol, limit=limit):
        signals = _reader().get_signals_by_symbol(symbol, limit)
        return SignalsResponse(symbol=symbol, signals=signals, count=len(signals))
