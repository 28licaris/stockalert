"""On-demand chart hydration for symbols that aren't in CH or the lake.

The bars gateway (`bars_gateway.get_chart_bars`) serves the two *stored*
tiers — ClickHouse (hot cache) and the S3 lake (`polygon_adjusted`,
a frozen whole-market snapshot from a one-month paid Polygon window).
A symbol that was never streamed *and* wasn't in that snapshot (e.g. a
just-listed or thinly-covered ticker like SIDU) returns empty from both.

This module adds the **third, live tier**: when AUTO yields nothing, pull
the window straight from the **Schwab price-history REST API** — the only
live provider we currently pay for — and serve it **directly**.

Design (why direct-serve, NOT a write-through cache): rendering a chart for
an ad-hoc, out-of-universe symbol needs *bars*, not a durable archive.
The archival path (`SchwabTipFill`) does a per-day PyIceberg commit to S3
plus a synchronous CH insert — tens of seconds for a 48-day pull, and it
blocks the event loop. That's right for background warmup, wrong for a
request. So here we just: async-fetch the requested window from Schwab,
resample in a worker thread, hold the result in a short in-memory TTL
cache, and return. No Iceberg write, no CH write — nothing on the loop
but the `aiohttp` fetch. Ad-hoc symbols don't self-heal into CH; a re-view
after the TTL re-fetches (a few seconds). If a symbol later joins the
stream universe, the real tip-fill / streaming path archives it properly.

Schwab's envelope shapes two granularities:
  - **Sub-daily (1m..1h):** minute bars reach back only ~48 days. We fetch
    the nearest native Schwab frequency (5m→5-min, etc.; 1h/4h from 30-min
    resampled up) and CLAMP the window to ~48d, logging the cap.
  - **Daily (1d):** native daily candles for a year+.

Production properties (this runs in the async request path, on the loop;
the Schwab client is `aiohttp`, so awaiting costs no thread and doesn't
block other users — the only cost is *this* client waiting a few seconds):
  - **Single-flight:** concurrent viewers / a symbol's own poll loop
    collapse to ONE upstream Schwab fetch per (symbol, interval).
  - **Disconnect-survival:** the fetch is a shielded task, so a client
    navigating away mid-fetch doesn't cancel it.
  - **Negative cache:** a symbol Schwab has no data for is remembered
    briefly so a polling chart doesn't re-hammer the API.

NOTE: single-flight / caches are process-local; a multi-instance deploy
would back them with Redis (the seam is the helpers at the bottom). The
MCP bars tool deliberately does NOT route through here — it calls the sync
gateway directly; agents get the stored tiers, not a live REST pull.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta, timezone
from typing import Awaitable, Callable, Optional

from app.services.readers.bars_gateway import BarSource, _resample, get_chart_bars
from app.services.readers.schemas import LiveBar

logger = logging.getLogger(__name__)

# Requested interval → (Schwab fetch timeframe, resample-to or None).
# Native frequencies keep payloads small; 1h/4h have no native Schwab
# frequency so we pull 30-min and resample up.
_SCHWAB_TF: dict[str, tuple[str, Optional[str]]] = {
    "1m":  ("1Min", None),
    "5m":  ("5Min", None),
    "15m": ("15Min", None),
    "30m": ("30Min", None),
    "1h":  ("30Min", "1h"),
    "4h":  ("30Min", "4h"),
    "1d":  ("1d", None),
}

# Schwab minute bars reach back ~48 calendar days; daily reaches years.
_SCHWAB_MINUTE_REACH_DAYS = 48
# Default window when the caller doesn't bound one.
_DEFAULT_INTRADAY_DAYS = 30
_DEFAULT_DAILY_DAYS = 365

# How long a directly-served result is reused before re-fetching, and how
# long "Schwab has no data for this symbol" is remembered.
_CACHE_TTL_S = 300.0
_NEGATIVE_TTL_S = 300.0

# ── process-local single-flight + caches (see module docstring) ──────────
_inflight: dict[tuple[str, str], "asyncio.Future"] = {}
_negative: dict[tuple[str, str], float] = {}
_cache: dict[tuple[str, str], tuple[float, list[LiveBar]]] = {}


async def get_chart_bars_hydrated(
    symbol: str,
    *,
    interval: str = "1m",
    lookback_days: Optional[int] = None,
    limit: Optional[int] = None,
    source: BarSource = BarSource.AUTO,
    reader=None,
) -> list[LiveBar]:
    """Async wrapper over `get_chart_bars` that adds a live Schwab tier.

    Serves the stored tiers first (CH + lake, via the sync gateway run
    off-thread). Only when AUTO returns nothing does it fall through to a
    direct Schwab fetch. `source=clickhouse|lake` pass straight through
    with no live pull.
    """
    bars = await asyncio.to_thread(
        get_chart_bars,
        symbol,
        interval=interval,
        lookback_days=lookback_days,
        limit=limit,
        source=source,
        reader=reader,
    )
    if bars or source != BarSource.AUTO:
        return bars

    sym = (symbol or "").strip().upper()
    if not sym or interval not in _SCHWAB_TF:
        return bars

    return await _hydrate(sym, interval=interval, lookback_days=lookback_days, limit=limit)


async def _hydrate(
    sym: str, *, interval: str, lookback_days: Optional[int], limit: Optional[int],
) -> list[LiveBar]:
    key = (sym, interval)

    cached = _cache_get(key)
    if cached is not None:
        logger.info("hydrate: %s %s served from TTL cache (%d bars)", sym, interval, len(cached))
        return _apply_limit(cached, limit)

    if _negative_cached(key):
        logger.info("hydrate: %s %s negative-cached; skipping live fetch", sym, interval)
        return []

    bars = await _single_flight(key, lambda: _fetch_and_cache(sym, interval, lookback_days))
    return _apply_limit(bars, limit)


async def _fetch_and_cache(
    sym: str, interval: str, lookback_days: Optional[int],
) -> list[LiveBar]:
    schwab_tf, resample_to = _SCHWAB_TF[interval]
    is_daily = schwab_tf == "1d"

    end = datetime.now(timezone.utc)
    days = lookback_days or (_DEFAULT_DAILY_DAYS if is_daily else _DEFAULT_INTRADAY_DAYS)
    start = end - timedelta(days=days)

    # Minute bars only reach ~48d — clamp and log rather than silently
    # over-request (and risk a Schwab error on too-wide a minute window).
    if not is_daily:
        floor = end - timedelta(days=_SCHWAB_MINUTE_REACH_DAYS)
        if start < floor:
            logger.info(
                "hydrate: %s %s window %dd exceeds Schwab's ~%dd minute reach; "
                "capping to %dd", sym, interval, days, _SCHWAB_MINUTE_REACH_DAYS,
                _SCHWAB_MINUTE_REACH_DAYS,
            )
            start = floor

    try:
        from app.config import get_provider

        provider = get_provider("schwab")
        df = await provider.historical_df(sym, start, end, timeframe=schwab_tf)
    except Exception as exc:  # noqa: BLE001 — provider boundary; degrade to empty
        logger.warning("hydrate: %s %s Schwab fetch failed: %s", sym, interval, exc)
        return []

    # df→LiveBar mapping + resample is CPU over potentially thousands of
    # rows — keep it off the event loop.
    bars = await asyncio.to_thread(_to_bars, df, sym, interval, resample_to)

    if not bars:
        logger.info(
            "hydrate: %s %s Schwab returned no data for %s..%s; negative-caching",
            sym, interval, start.date(), end.date(),
        )
        _set_negative((sym, interval))
        return []

    _cache_set((sym, interval), bars)
    logger.info(
        "hydrate: %s %s fetched %d bars from Schwab (%s, %s..%s)",
        sym, interval, len(bars), schwab_tf, start.date(), end.date(),
    )
    return bars


def _to_bars(df, sym: str, interval: str, resample_to: Optional[str]) -> list[LiveBar]:
    """Map a Schwab `historical_df` frame (ts-indexed, ascending) to
    `LiveBar`s, then resample up if the native frequency is finer than the
    requested interval. Schwab candles carry no vwap/trade_count."""
    if df is None or getattr(df, "empty", True):
        return []
    out: list[LiveBar] = []
    for ts, row in df.iterrows():
        t = ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        out.append(
            LiveBar(
                symbol=sym,
                timestamp=t,
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row["volume"]),
                vwap=None,
                trade_count=None,
                source="schwab-ondemand",
                interval=interval,
            )
        )
    if resample_to:
        out = _resample(out, resample_to)
    return out


def _apply_limit(bars: list[LiveBar], limit: Optional[int]) -> list[LiveBar]:
    """Newest-anchored cap, matching the gateway's auto-limit behavior."""
    if limit is not None and len(bars) > limit:
        return bars[-limit:]
    return bars


# ─────────────────────────────────────────────────────────────────────
# Single-flight + caches (process-local; Redis seam for multi-instance)
# ─────────────────────────────────────────────────────────────────────


async def _single_flight(
    key: tuple[str, str], factory: Callable[[], Awaitable],
):
    """Run `factory()` at most once per `key` while it's in flight; all
    concurrent callers await the same task. The task is shielded so a
    cancelled caller (client disconnect) doesn't kill the shared work."""
    task = _inflight.get(key)
    if task is None or task.done():
        task = asyncio.ensure_future(factory())
        _inflight[key] = task

        def _cleanup(t: "asyncio.Future", k=key) -> None:
            if _inflight.get(k) is t:
                _inflight.pop(k, None)

        task.add_done_callback(_cleanup)
    return await asyncio.shield(task)


def _negative_cached(key: tuple[str, str]) -> bool:
    exp = _negative.get(key)
    if exp is None:
        return False
    if time.monotonic() >= exp:
        _negative.pop(key, None)
        return False
    return True


def _set_negative(key: tuple[str, str]) -> None:
    _negative[key] = time.monotonic() + _NEGATIVE_TTL_S


def _cache_get(key: tuple[str, str]) -> Optional[list[LiveBar]]:
    item = _cache.get(key)
    if item is None:
        return None
    exp, bars = item
    if time.monotonic() >= exp:
        _cache.pop(key, None)
        return None
    return bars


def _cache_set(key: tuple[str, str], bars: list[LiveBar]) -> None:
    _cache[key] = (time.monotonic() + _CACHE_TTL_S, bars)


def _reset_caches() -> None:
    """Test hook — clear all process-local single-flight / cache state."""
    _inflight.clear()
    _negative.clear()
    _cache.clear()
