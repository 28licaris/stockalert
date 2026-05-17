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
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.services.readers.schemas import LiveBar

logger = logging.getLogger(__name__)


# Supported intervals match `app.db.queries.SUPPORTED_INTERVALS` so
# routes can pass the same names through unchanged.
_SUPPORTED_INTERVALS = {"1m", "5m", "15m", "30m", "1h", "4h", "1d"}

# Approximate bars-per-day per interval, used by `get_bars_for_chart`
# to auto-size the row limit when the caller specifies `lookback_days`
# but not `limit`. ~16h coverage (regular + pre/post market) — keeps the
# auto-limit from ballooning unnecessarily.
_BARS_PER_DAY: dict[str, int] = {
    "1m": 16 * 60,   # ~960
    "5m": 16 * 12,   # ~192
    "15m": 16 * 4,   # ~64
    "30m": 16 * 2,   # ~32
    "1h": 16,
    "4h": 4,
    "1d": 1,
}
_LIMIT_HARD_CAP = 100_000

# Schwab's pricehistory caps 1-min bars at ~48 days. For longer lookbacks
# we resample from `ohlcv_5m` (which extends ~270 days) instead.
_USE_5M_SOURCE_OVER_DAYS = 48

# Sentinel "open lower bound" used by get_bars_for_chart when the caller
# didn't specify a lookback. Any timestamp safely earlier than the
# oldest data in CH.
_MIN_TS = datetime(2000, 1, 1, tzinfo=timezone.utc)


def _ensure_utc(ts: datetime) -> datetime:
    """Normalize a datetime to tz-aware UTC. See `bronze_reader._ensure_utc`."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts.astimezone(timezone.utc)


def _row_to_live_bar(row: dict, interval: str, *, symbol: Optional[str] = None) -> LiveBar:
    """
    Convert a `dict` from `app.db.queries` into a `LiveBar`. The
    underlying queries vary on column names and subsets:

      - `list_bars_resampled` returns `ts` (not `timestamp`) and omits
        `symbol`, `vwap`, `trade_count`, `source`.
      - `fetch_bars` / `list_bars_desc` use `timestamp`, also omit
        symbol-level metadata.
      - `latest_bar_per_symbol` includes `symbol` and `ts`.
      - `list_daily_bars` uses `timestamp`.

    We coalesce: `timestamp` or `ts` for the time field; missing
    `symbol` defaulted from the caller; missing metadata -> None.
    """
    ts = row.get("timestamp") if "timestamp" in row else row.get("ts")
    return LiveBar(
        symbol=row.get("symbol") or symbol or "",
        timestamp=ts,
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
        limit: int = _LIMIT_HARD_CAP,
        source_table: Optional[str] = None,
    ) -> list[LiveBar]:
        """
        Return bars for `symbol` in the half-open window `[start, end)`
        at the requested `interval`. Supported intervals match
        `queries.SUPPORTED_INTERVALS`: `'1m'`, `'5m'`, `'15m'`, `'30m'`,
        `'1h'`, `'4h'`, `'1d'`.

        Routing:
          - `interval='1d'` -> `queries.list_daily_bars`
            (the dedicated `ohlcv_daily` table; Schwab daily history
            extends 20+ years).
          - Everything else -> `queries.list_bars_resampled`. With
            `interval='1m'` and `source_table='ohlcv_1m'` (the default)
            this is a pass-through; for longer intervals it does the
            ClickHouse-side rollup.

        `source_table` lets callers force the source ('ohlcv_1m' for
        the highest fidelity / shortest window; 'ohlcv_5m' for windows
        > ~48 days where the 1m table doesn't have history). Omit it
        to let the reader pick — `get_bars_for_chart` uses this.

        Naive datetimes treated as UTC. `end <= start` -> `[]`.
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

        if interval == "1d":
            rows = queries.list_daily_bars(symbol, start_utc, end_utc, limit)
        else:
            kwargs: dict = {}
            if source_table is not None:
                kwargs["source_table"] = source_table
            rows = queries.list_bars_resampled(
                symbol, interval, start_utc, end_utc, limit, **kwargs
            )
        if not rows:
            return []
        return [_row_to_live_bar(r, interval, symbol=symbol) for r in rows]

    def get_bars_for_chart(
        self,
        symbol: str,
        *,
        interval: str = "1m",
        lookback_days: Optional[int] = None,
        limit: Optional[int] = None,
    ) -> list[LiveBar]:
        """
        Higher-level helper for chart endpoints. Owns the multi-table
        fallback + auto-limit logic that previously lived in the
        `/api/bars` route.

        Window selection:
          - If `lookback_days` is set, window is `[now-N, now]`.
          - If not, no window filter — caller gets `limit` most recent
            bars across all available history.

        Source selection:
          - `interval='1d'` -> try `ohlcv_daily`; fall back to a
            resampled query if the daily table is empty (useful before
            the first daily backfill completes).
          - `interval='1m'` -> always use `ohlcv_1m` as source.
          - `interval='5m'..'4h'` with `lookback_days > 48` -> prefer
            `ohlcv_5m` (Schwab 5m extends ~270d). Fall back to
            `ohlcv_1m` if 5m is empty.
          - `interval='5m'..'4h'` with `lookback_days <= 48` or None ->
            use `ohlcv_1m` (highest fidelity for recent data).

        Auto-limit when caller omits `limit`:
          - No lookback -> 500 rows (the historical default).
          - With lookback -> `~bars_per_day * lookback * 1.5`, capped
            at 100k. The 1.5x headroom covers pre/post-market and
            weekend skips.

        Raises `ValueError` on unknown interval.
        """
        if interval not in _SUPPORTED_INTERVALS:
            supported = ", ".join(sorted(_SUPPORTED_INTERVALS))
            raise ValueError(
                f"Unknown interval {interval!r}. Supported: {supported}."
            )

        end: Optional[datetime] = None
        start: Optional[datetime] = None
        if lookback_days is not None:
            end = datetime.now(timezone.utc)
            start = end - timedelta(days=lookback_days)

        if limit is None:
            if lookback_days is None:
                limit = 500
            else:
                per_day = _BARS_PER_DAY.get(interval, 16)
                limit = min(_LIMIT_HARD_CAP, max(500, int(lookback_days * per_day * 1.5)))

        # Daily: native table with resampled fallback (handles the
        # pre-first-daily-backfill case where ohlcv_daily is empty but
        # ohlcv_1m has bars covering the same window).
        if interval == "1d":
            bars = self.get_bars_in_range(
                symbol,
                start or _MIN_TS,
                end or datetime.now(timezone.utc),
                interval="1d",
                limit=limit,
            )
            if bars:
                return bars
            from app.db import queries
            rows = queries.list_bars_resampled(
                symbol,
                "1d",
                start or _MIN_TS,
                end or datetime.now(timezone.utc),
                limit,
                source_table="ohlcv_1m",
            )
            return [_row_to_live_bar(r, "1d", symbol=symbol) for r in rows]

        # 1m: always from ohlcv_1m.
        if interval == "1m":
            return self.get_bars_in_range(
                symbol,
                start or _MIN_TS,
                end or datetime.now(timezone.utc),
                interval="1m",
                limit=limit,
                source_table="ohlcv_1m",
            )

        # 5m..4h: pick source by lookback. Schwab 1m caps at ~48d; 5m extends ~270d.
        prefer_5m = lookback_days is not None and lookback_days > _USE_5M_SOURCE_OVER_DAYS
        if prefer_5m:
            bars = self.get_bars_in_range(
                symbol,
                start or _MIN_TS,
                end or datetime.now(timezone.utc),
                interval=interval,
                limit=limit,
                source_table="ohlcv_5m",
            )
            if bars:
                return bars
            # 5m table empty (first visit); fall back to 1m source.
            return self.get_bars_in_range(
                symbol,
                start or _MIN_TS,
                end or datetime.now(timezone.utc),
                interval=interval,
                limit=limit,
                source_table="ohlcv_1m",
            )

        return self.get_bars_in_range(
            symbol,
            start or _MIN_TS,
            end or datetime.now(timezone.utc),
            interval=interval,
            limit=limit,
            source_table="ohlcv_1m",
        )

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
