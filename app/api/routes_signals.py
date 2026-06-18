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
from app.services.readers.bars_gateway import BarSource
from app.services.readers.bars_hydration import get_chart_bars_hydrated
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
    source: BarSource = Query(
        BarSource.AUTO,
        description=(
            "Data tier. 'auto' (default): ClickHouse first, fill the "
            "window from the S3 lake on a miss, re-query CH. "
            "'clickhouse': hot cache only (fast, may be partial). "
            "'lake': S3 ground truth only (complete history; no CH write)."
        ),
    ),
    reader: BarReader = Depends(get_bar_reader),
) -> list[Bar]:
    """
    Return OHLCV bars for `symbol` at `interval`, routed across the
    ClickHouse hot cache and the S3 lake by `source` (see param). The
    CH-vs-S3 routing lives in `bars_gateway.get_chart_bars` so this
    route and the MCP `get_bars_for_chart` tool behave identically.

    Default `auto` is CH-first with on-demand lake fill, then a live
    Schwab REST pull when both stored tiers miss (e.g. an out-of-universe
    symbol never in the frozen Polygon snapshot): ad-hoc and universe
    symbols both chart correctly, and CH warms for subsequent requests.
    `lake` reads ground truth without writing CH; neither `lake` nor
    `clickhouse` triggers the live Schwab tier.
    """
    if interval not in queries.SUPPORTED_INTERVALS:
        raise HTTPException(
            400,
            f"Unsupported interval {interval!r}. "
            f"Allowed: {sorted(queries.SUPPORTED_INTERVALS.keys())}",
        )

    bars = await get_chart_bars_hydrated(
        symbol,
        interval=interval,
        lookback_days=lookback_days,
        limit=limit,
        source=source,
        reader=reader,
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


@router.get("/bars/latest")
async def latest_bars(
    symbols: str = Query(
        ...,
        description="Comma-separated tickers, e.g. 'AAPL,MSFT,NVDA'.",
    ),
) -> dict:
    """Latest streamed 1-minute bar per symbol from ClickHouse (`last` = close).

    A single fast query (~tens of ms) for tables that show a "last" column over
    many symbols — e.g. the stream-universe page. Reads only the hot tier and
    makes **no live-provider call**, so it stays sub-100ms regardless of symbol
    count and never blocks on Schwab. Symbols with no CH bars are simply absent
    from `items` (the caller renders them as "—").
    """
    syms = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not syms:
        return {"items": []}
    rows = await queries.latest_close_per_symbol_async(syms)
    return {
        "items": [
            {"symbol": r["symbol"], "last": r["last"], "ts": _ts(r["ts"])}
            for r in rows
        ]
    }
