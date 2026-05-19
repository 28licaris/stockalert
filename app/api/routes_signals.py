"""
Live signals + bar-chart routes.

Both endpoints are thin adapters now:
  /api/signals  ->  SignalReader.get_signals_by_symbol
  /api/bars     ->  BarReader.get_bars_for_chart

The reader services own the SQL + routing logic; this module shapes
the HTTP response. Response shapes are preserved exactly for backward
compatibility with the existing dashboard JS.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.schemas.bars import Bar
from app.api.schemas.signals import Signal
from app.db import queries  # only for SUPPORTED_INTERVALS (validation)
from app.services.readers.bar_reader import BarReader
from app.services.readers.signal_reader import SignalReader

logger = logging.getLogger(__name__)

router = APIRouter()


# ─────────────────────────────────────────────────────────────────────
# Dependency providers (override in tests)
# ─────────────────────────────────────────────────────────────────────


def get_signal_reader() -> SignalReader:
    return SignalReader.from_settings()


def get_bar_reader() -> BarReader:
    return BarReader.from_settings()


# ─────────────────────────────────────────────────────────────────────
# Response shaping helpers
# ─────────────────────────────────────────────────────────────────────


def _ts(v: Any) -> Optional[str]:
    """
    ISO-format a datetime, forcing a `Z` suffix so JS `new Date(...)`
    doesn't interpret naive ClickHouse timestamps as local time.
    """
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        if getattr(v, "tzinfo", None) is None:
            return v.isoformat() + "Z"
        return v.isoformat()
    return str(v)


# ─────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────


@router.get("/signals", response_model=list[Signal])
async def list_signals(
    symbol: Optional[str] = None,
    limit: int = 50,
    reader: SignalReader = Depends(get_signal_reader),
) -> list[Signal]:
    """
    Return recent signals (optionally filtered to `symbol`),
    newest-first. Response shape preserved verbatim for the dashboard.
    """
    signals = await asyncio.to_thread(
        reader.get_signals_by_symbol, symbol, limit
    )
    return [
        Signal(
            symbol=s.symbol,
            type=s.signal_type,
            indicator=s.indicator,
            ts=_ts(s.ts_signal) or "",
            price=s.price_at_signal,
            indicator_value=s.indicator_value,
        )
        for s in signals
    ]


@router.get("/bars", response_model=list[Bar])
async def list_bars(
    symbol: str,
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=100_000,
        description=(
            "Max rows to return. When omitted and `lookback_days` is set, "
            "the server picks a sensible cap based on interval×lookback."
        ),
    ),
    interval: str = Query(
        "1m",
        description=f"Bar interval. One of: {', '.join(queries.SUPPORTED_INTERVALS.keys())}",
    ),
    lookback_days: Optional[int] = Query(
        None,
        ge=1,
        le=20000,
        description="Restrict to bars in the last N days (server-side window).",
    ),
    reader: BarReader = Depends(get_bar_reader),
) -> list[Bar]:
    """
    Return OHLCV bars for `symbol` at `interval`. All source-table
    selection, fallback, and auto-limit logic lives in
    `BarReader.get_bars_for_chart`; this route validates input, calls
    the reader, and reshapes the response for the dashboard.
    """
    if interval not in queries.SUPPORTED_INTERVALS:
        raise HTTPException(
            400,
            f"Unsupported interval {interval!r}. "
            f"Allowed: {sorted(queries.SUPPORTED_INTERVALS.keys())}",
        )

    bars = await asyncio.to_thread(
        reader.get_bars_for_chart,
        symbol,
        interval=interval,
        lookback_days=lookback_days,
        limit=limit,
    )
    return [
        Bar(
            ts=_ts(b.timestamp) or "",
            open=b.open,
            high=b.high,
            low=b.low,
            close=b.close,
            volume=b.volume,
            vwap=b.vwap,
            trade_count=b.trade_count,
            source=b.source,
        )
        for b in bars
    ]
