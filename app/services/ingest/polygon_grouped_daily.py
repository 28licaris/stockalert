"""
Polygon REST grouped-daily → canonical OHLCV frame.

`/v2/aggs/grouped/locale/us/market/stocks/{date}?adjusted=false` returns the
WHOLE US market's daily bars (~12.4k symbols) in one call — the daily-bar
source of record after the flat-files subscription lapsed (2026-07-01).
Raw (unadjusted) rows land in `equities.polygon_daily_raw` via
`EquitiesIcebergSink.for_polygon_daily_raw()`; split adjustment happens at
the ClickHouse load (scripts/refresh_ohlcv_daily.py), same as everywhere else.

The bar's `timestamp` is the ET trading day at 14:30 UTC — the same
convention as CH `ohlcv_daily`, so downstream date bucketing is identical.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Iterable

import pandas as pd

from app.config import settings

SOURCE_TAG = "polygon-rest"
_DAY_TS_UTC_HOUR, _DAY_TS_UTC_MIN = 14, 30  # ET date at 14:30 UTC (ohlcv_daily convention)


def day_timestamp_utc(d: date) -> datetime:
    """The canonical UTC timestamp used for daily bars of ET trading day `d`."""
    return datetime(d.year, d.month, d.day, _DAY_TS_UTC_HOUR, _DAY_TS_UTC_MIN,
                    tzinfo=timezone.utc)


def _field(row: Any, attr: str, short_key: str) -> Any:
    """Read a value from an SDK GroupedDailyAgg (long attr names) or a raw
    JSON dict (Polygon's short keys, e.g. T/o/h/l/c/v/vw/n)."""
    if isinstance(row, dict):
        v = row.get(short_key)
        return row.get(attr) if v is None else v
    return getattr(row, attr, None)


def grouped_daily_to_frame(rows: Iterable[Any], day: date) -> pd.DataFrame:
    """Pure transform: grouped-daily rows → the sink's canonical-shape frame.
    Rows without a symbol or close are dropped (data-quality boundary);
    0.0 vwap becomes NULL downstream (sink placeholder convention)."""
    ts = day_timestamp_utc(day)
    out = []
    for r in rows:
        sym = _field(r, "ticker", "T")
        close = _field(r, "close", "c")
        if not sym or close is None:
            continue
        n_trades = _field(r, "transactions", "n")
        out.append({
            "symbol": str(sym).upper(),
            "timestamp": ts,
            "open": _field(r, "open", "o"),
            "high": _field(r, "high", "h"),
            "low": _field(r, "low", "l"),
            "close": float(close),
            "volume": float(_field(r, "volume", "v") or 0.0),
            "vwap": _field(r, "vwap", "vw"),
            "trade_count": int(n_trades) if n_trades is not None else None,
            "source": SOURCE_TAG,
        })
    return pd.DataFrame(out, columns=[
        "symbol", "timestamp", "open", "high", "low", "close",
        "volume", "vwap", "trade_count", "source",
    ])


def fetch_grouped_daily(day: date, *, api_key: str | None = None) -> pd.DataFrame:
    """One REST call → the whole market's daily bars for `day` (unadjusted).
    Weekends/holidays return an empty frame (Polygon: resultsCount=0)."""
    from massive import RESTClient  # the repo's Polygon SDK (see polygon_provider)
    key = api_key or settings.polygon_api_key
    if not key:
        raise RuntimeError("POLYGON_API_KEY is not configured")
    client = RESTClient(api_key=key)
    rows = client.get_grouped_daily_aggs(day.isoformat(), adjusted=False)
    return grouped_daily_to_frame(rows or [], day)
