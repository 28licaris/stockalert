import asyncio
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from app.db import queries

router = APIRouter()


def _ts(v):
    """ISO-format a datetime, forcing a UTC marker so JS `new Date(...)` doesn't
    interpret naive ClickHouse timestamps as local time."""
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        if getattr(v, "tzinfo", None) is None:
            return v.isoformat() + "Z"
        return v.isoformat()
    return str(v)


@router.get("/signals")
async def list_signals(symbol: str | None = None, limit: int = 50):
    rows = await asyncio.to_thread(queries.list_signals, symbol, limit)
    return [
        {
            "id": x["id"],
            "symbol": x["symbol"],
            "type": x["type"],
            "indicator": x["indicator"],
            "ts": _ts(x["ts"]),
            "price": x["price"],
            "indicator_value": x["indicator_value"],
        }
        for x in rows
    ]


# Approximate "active bars per calendar day" for each interval. Used to scale
# the implicit `limit` when `lookback_days` is given but `limit` is not. We use
# ~16h coverage (regular + pre/post market) instead of 24h so the auto-limit
# doesn't balloon unnecessarily.
_BARS_PER_DAY: dict[str, int] = {
    "1m":  16 * 60,        # ~960
    "5m":  16 * 12,        # ~192
    "15m": 16 * 4,         # ~64
    "30m": 16 * 2,         # ~32
    "1h":  16,             # ~16
    "4h":  4,              # ~4
    "1d":  1,
}
_LIMIT_HARD_CAP = 100_000


@router.get("/bars")
async def list_bars(
    symbol: str,
    limit: Optional[int] = Query(
        None,
        ge=1,
        le=_LIMIT_HARD_CAP,
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
):
    """
    Return OHLCV bars for `symbol` at `interval`.

    Routing:
      - interval='1d': read from native `ohlcv_daily` table (Schwab daily bars
        cover 20+ years). Falls back to resampled `ohlcv_1m` if the daily
        table is empty - useful before the first daily backfill completes.
      - interval='1m': read from `ohlcv_1m` (the only place 1-min lives).
      - interval='5m'..'4h':
          * lookback ≤ 48d -> resample from `ohlcv_1m` (highest fidelity).
          * lookback > 48d  -> resample from `ohlcv_5m` (Schwab 1m caps at ~48d,
            but 5m extends ~270d). Falls back to `ohlcv_1m` if 5m empty.
    """
    if interval not in queries.SUPPORTED_INTERVALS:
        raise HTTPException(
            400,
            f"Unsupported interval {interval!r}. "
            f"Allowed: {sorted(queries.SUPPORTED_INTERVALS.keys())}",
        )

    end: Optional[datetime] = None
    start: Optional[datetime] = None
    if lookback_days is not None:
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=lookback_days)

    # Auto-size the limit so a long lookback doesn't get truncated by the
    # historical 500-row default. The previous behavior was confusing: ask for
    # "1h / 270d" and get only 30 days back because 500 hourly rows ≈ 30d.
    if limit is None:
        if lookback_days is None:
            limit = 500
        else:
            per_day = _BARS_PER_DAY.get(interval, 16)
            # 1.5x headroom for pre/post-market + weekends not skipping cleanly.
            limit = min(_LIMIT_HARD_CAP, max(500, int(lookback_days * per_day * 1.5)))

    # Storage routing:
    #   1m            -> ohlcv_1m (only place 1-min is available)
    #   5m..4h, <=48d -> ohlcv_1m resampled (highest fidelity for recent data)
    #   5m..4h, >48d  -> ohlcv_5m resampled (Schwab 1m cap is ~48d; 5m goes 270d)
    #   1d            -> ohlcv_daily native (Schwab daily goes 20+ years)
    # All paths fall back to the next-best source if the primary table is empty
    # so the page still renders during initial backfill.
    if interval == "1d":
        raw = await asyncio.to_thread(
            queries.list_daily_bars, symbol, start, end, limit
        )
        if not raw:
            raw = await asyncio.to_thread(
                queries.list_bars_resampled, symbol, interval, start, end, limit
            )
    elif interval == "1m":
        raw = await asyncio.to_thread(
            queries.list_bars_resampled, symbol, interval, start, end, limit
        )
    else:
        # Pick source by lookback window.
        prefer_5m = (lookback_days is not None and lookback_days > 48)
        if prefer_5m:
            raw = await asyncio.to_thread(
                queries.list_bars_resampled, symbol, interval, start, end, limit,
                source_table="ohlcv_5m",
            )
            if not raw:
                # 5m table empty (first visit); fall back to 1m source.
                raw = await asyncio.to_thread(
                    queries.list_bars_resampled, symbol, interval, start, end, limit,
                    source_table="ohlcv_1m",
                )
        else:
            raw = await asyncio.to_thread(
                queries.list_bars_resampled, symbol, interval, start, end, limit,
                source_table="ohlcv_1m",
            )

    return [
        {
            "ts": _ts(x["ts"]),
            "open": x["open"],
            "high": x["high"],
            "low": x["low"],
            "close": x["close"],
            "volume": x["volume"],
        }
        for x in raw
    ]
