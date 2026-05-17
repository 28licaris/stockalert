"""
BarReader — read service for ClickHouse `ohlcv_*` tables (the live tier).

Counterpart to `BronzeReader` for **recent** data: same Pydantic shape
philosophy (`LiveBar` parallels `BronzeBar`), CH-bound implementation,
seconds-fresh latency. The two readers are siblings, not a hierarchy —
callers pick the tier appropriate for their query (live UI = BarReader,
ML training = BronzeReader).

Design intent (see `feedback_platform_design_intent`):

  - Thin wrappers over `app.db.queries` functions. No SQL in the
    reader — the queries module owns the SQL; the reader owns the
    Pydantic conversion and the public contract.
  - Result objects, not raises. Empty result -> `[]`; bad input
    (unknown interval) -> `ValueError`; CH outage -> wrapped
    exception bubbles up (the route layer maps it to 500).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.services.readers.schemas import LiveBar

logger = logging.getLogger(__name__)


# Which CH table backs each interval. 1m and 5m have their own tables;
# longer intervals are resampled from 5m at query time.
_DIRECT_INTERVAL_TABLES = {
    "1m": "ohlcv_1m",
    "5m": "ohlcv_5m",
    "daily": "ohlcv_daily",
}
_RESAMPLE_INTERVALS = {"15m", "30m", "1h", "4h"}
_SUPPORTED_INTERVALS = set(_DIRECT_INTERVAL_TABLES) | _RESAMPLE_INTERVALS


def _ensure_utc(ts: datetime) -> datetime:
    """Normalize a datetime to tz-aware UTC. See `bronze_reader._ensure_utc`."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _row_to_live_bar(row: dict, interval: str, *, symbol: Optional[str] = None) -> LiveBar:
    """
    Convert a `dict` from `app.db.queries` into a `LiveBar`. CH queries
    return varying column subsets depending on which function was used
    (some omit `vwap` / `trade_count` / `source`); we coalesce missing
    fields to None / interval default.
    """
    return LiveBar(
        symbol=row.get("symbol") or symbol or "",
        timestamp=row["timestamp"],
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row.get("volume") or 0.0),
        vwap=(float(row["vwap"]) if row.get("vwap") not in (None, 0, 0.0) else None),
        trade_count=(int(row["trade_count"]) if row.get("trade_count") not in (None, 0) else None),
        source=row.get("source") or None,
        interval=interval,
    )


class BarReader:
    """
    Read interface over ClickHouse live-tier bar tables.

    Stateless; uses `app.db.queries` (which manages its own CH client
    via `app.db.client.get_client()`).
    """

    @classmethod
    def from_settings(cls) -> "BarReader":
        """Production construction path; the CH client is managed elsewhere."""
        return cls()

    def get_recent_bars(self, symbol: str, limit: int = 200) -> list[LiveBar]:
        """
        Return the most recent `limit` 1-minute bars for `symbol`,
        sorted oldest-first. Wraps `queries.list_bars_desc` and reverses
        for ascending order (UIs and indicator code expect ASC).

        Empty if `symbol` has no bars in CH.
        """
        if limit <= 0:
            return []
        from app.db import queries  # lazy: avoids pulling CH client at import time

        rows = queries.list_bars_desc(symbol, limit)
        if not rows:
            return []
        bars = [_row_to_live_bar(r, "1m", symbol=symbol) for r in rows]
        bars.sort(key=lambda b: b.timestamp)
        return bars

    def get_bars_in_range(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        *,
        interval: str = "1m",
        limit: int = 100_000,
    ) -> list[LiveBar]:
        """
        Return bars for `symbol` in the half-open window `[start, end)`
        at the requested `interval`. Supported intervals:
          - Direct tables: '1m', '5m', 'daily'
          - Resampled at query time: '15m', '30m', '1h', '4h'

        Naive datetimes treated as UTC.
        """
        if interval not in _SUPPORTED_INTERVALS:
            supported = ", ".join(sorted(_SUPPORTED_INTERVALS))
            raise ValueError(
                f"Unknown interval {interval!r}. Supported: {supported}."
            )
        start_utc = _ensure_utc(start)
        end_utc = _ensure_utc(end)
        if end_utc <= start_utc:
            return []

        from app.db import queries

        if interval == "daily":
            rows = queries.list_daily_bars(symbol, start_utc, end_utc, limit)
        elif interval in _DIRECT_INTERVAL_TABLES:
            df = queries.fetch_bars(symbol, start_utc, end_utc, limit)
            rows = df.to_dict(orient="records") if not df.empty else []
        else:
            rows = queries.list_bars_resampled(
                symbol, start_utc, end_utc, interval=interval, limit=limit
            )
        if not rows:
            return []
        return [_row_to_live_bar(r, interval, symbol=symbol) for r in rows]

    def get_latest_bar_per_symbol(self, symbols: list[str]) -> dict[str, LiveBar]:
        """
        Return the most recent 1m bar for each requested symbol. Symbols
        with no rows are omitted from the result (caller diffs against
        requested set to detect gaps).
        """
        if not symbols:
            return {}
        from app.db import queries

        rows = queries.latest_bar_per_symbol(symbols)
        out: dict[str, LiveBar] = {}
        for r in rows:
            sym = r.get("symbol")
            if not sym:
                continue
            out[sym] = _row_to_live_bar(r, "1m", symbol=sym)
        return out
