"""Typed ClickHouse reads/writes for OHLCV and signals."""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime, timezone
from typing import Any, List, Optional

import pandas as pd

from app.db.client import get_client


def _now_version() -> int:
    return time.time_ns() // 1_000_000


_INTRADAY_COLUMNS = [
    "symbol",
    "timestamp",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "vwap",
    "trade_count",
    "source",
    "version",
]


def _insert_intraday(table: str, rows: List[dict]) -> None:
    """Generic 1-min / 5-min insert; both tables share the same column layout."""
    if not rows:
        return
    client = get_client()
    ver = _now_version()
    data: List[List[Any]] = []
    for r in rows:
        data.append(
            [
                r["symbol"],
                r["timestamp"],
                float(r["open"]),
                float(r["high"]),
                float(r["low"]),
                float(r["close"]),
                float(r["volume"]),
                float(r.get("vwap") or 0),
                int(r.get("trade_count") or 0),
                str(r.get("source") or ""),
                int(r.get("version") or ver),
            ]
        )
    client.insert(table, data, column_names=_INTRADAY_COLUMNS)


def insert_bars_batch(rows: List[dict]) -> None:
    """Insert 1-min bars into `ohlcv_1m`."""
    _insert_intraday("ohlcv_1m", rows)


def insert_5m_bars_batch(rows: List[dict]) -> None:
    """Insert native 5-min bars into `ohlcv_5m`."""
    _insert_intraday("ohlcv_5m", rows)


async def insert_5m_bars_batch_async(rows: List[dict]) -> None:
    await asyncio.to_thread(insert_5m_bars_batch, rows)


def insert_signals_batch(rows: List[dict]) -> None:
    if not rows:
        return
    client = get_client()
    data: List[List[Any]] = []
    for r in rows:
        rid = r.get("id")
        if rid is None:
            rid = uuid.uuid4()
        elif isinstance(rid, str):
            rid = uuid.UUID(rid)
        data.append(
            [
                rid,
                r["symbol"],
                r["signal_type"],
                r["indicator"],
                r["ts_signal"],
                float(r["price_at_signal"]),
                float(r["indicator_value"]),
                r["p1_ts"],
                r["p2_ts"],
            ]
        )
    client.insert(
        "signals",
        data,
        column_names=[
            "id",
            "symbol",
            "signal_type",
            "indicator",
            "ts_signal",
            "price_at_signal",
            "indicator_value",
            "p1_ts",
            "p2_ts",
        ],
    )


def fetch_bars(
    symbol: str,
    start: datetime,
    end: datetime,
    limit: int,
) -> pd.DataFrame:
    client = get_client()
    result = client.query(
        """
        SELECT timestamp, open, high, low, close, volume
        FROM ohlcv_1m FINAL
        WHERE symbol = {sym:String}
          AND timestamp >= {start:DateTime64(3)}
          AND timestamp <= {end:DateTime64(3)}
        ORDER BY timestamp
        LIMIT {lim:UInt32}
        """,
        parameters={"sym": symbol, "start": start, "end": end, "lim": limit},
    )
    if not result.result_rows:
        return pd.DataFrame()
    df = pd.DataFrame(
        result.result_rows,
        columns=["timestamp", "open", "high", "low", "close", "volume"],
    )
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    df.set_index("timestamp", inplace=True)
    return df


def list_bars_desc(symbol: str, limit: int) -> List[dict]:
    client = get_client()
    result = client.query(
        """
        SELECT timestamp, open, high, low, close, volume
        FROM ohlcv_1m FINAL
        WHERE symbol = {sym:String}
        ORDER BY timestamp DESC
        LIMIT {lim:UInt32}
        """,
        parameters={"sym": symbol, "lim": limit},
    )
    out = []
    for row in result.result_rows:
        ts, o, h, l, c, v = row
        out.append(
            {
                "ts": ts,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": int(v) if v is not None else 0,
            }
        )
    return list(reversed(out))


# Map interval strings to ClickHouse INTERVAL expressions. We keep the storage
# tables at fixed timeframes (1-min and 1-day) and aggregate on read for the
# intra-day intervals; `toStartOfInterval` is O(rows) and fast.
SUPPORTED_INTERVALS: dict[str, str] = {
    "1m":  "INTERVAL 1 MINUTE",
    "5m":  "INTERVAL 5 MINUTE",
    "15m": "INTERVAL 15 MINUTE",
    "30m": "INTERVAL 30 MINUTE",
    "1h":  "INTERVAL 1 HOUR",
    "4h":  "INTERVAL 4 HOUR",
    "1d":  "INTERVAL 1 DAY",
}


def insert_daily_bars_batch(rows: List[dict]) -> None:
    if not rows:
        return
    client = get_client()
    ver = _now_version()
    data: List[List[Any]] = []
    for r in rows:
        data.append(
            [
                r["symbol"],
                r["timestamp"],
                float(r["open"]),
                float(r["high"]),
                float(r["low"]),
                float(r["close"]),
                float(r["volume"]),
                str(r.get("source") or ""),
                int(r.get("version") or ver),
            ]
        )
    client.insert(
        "ohlcv_daily",
        data,
        column_names=[
            "symbol", "timestamp", "open", "high", "low", "close",
            "volume", "source", "version",
        ],
    )


async def insert_daily_bars_batch_async(rows: List[dict]) -> None:
    await asyncio.to_thread(insert_daily_bars_batch, rows)


def list_daily_bars(
    symbol: str,
    start: Optional[datetime],
    end: Optional[datetime],
    limit: int,
) -> List[dict]:
    """Read native daily bars from `ohlcv_daily` (no resampling)."""
    where_parts = ["symbol = {sym:String}"]
    params: dict = {"sym": symbol.upper(), "lim": int(limit)}
    if start is not None:
        where_parts.append("timestamp >= {start:DateTime64(3)}")
        params["start"] = start
    if end is not None:
        where_parts.append("timestamp <= {end:DateTime64(3)}")
        params["end"] = end
    sql = f"""
        SELECT timestamp, open, high, low, close, volume
        FROM ohlcv_daily FINAL
        WHERE {' AND '.join(where_parts)}
        ORDER BY timestamp DESC
        LIMIT {{lim:UInt32}}
    """
    result = get_client().query(sql, parameters=params)
    out: List[dict] = []
    for row in result.result_rows:
        ts, o, h, l, c, v = row
        out.append({
            "ts": ts,
            "open": float(o) if o is not None else None,
            "high": float(h) if h is not None else None,
            "low": float(l) if l is not None else None,
            "close": float(c) if c is not None else None,
            "volume": int(v) if v is not None else 0,
        })
    return list(reversed(out))


def daily_coverage(symbol: str, start: datetime, end: datetime) -> dict:
    """Coverage report against `ohlcv_daily`. Same shape as `coverage()`."""
    result = get_client().query(
        """
        SELECT min(timestamp), max(timestamp), count()
        FROM ohlcv_daily FINAL
        WHERE symbol = {sym:String}
          AND timestamp >= {start:DateTime64(3)}
          AND timestamp <= {end:DateTime64(3)}
        """,
        parameters={"sym": symbol.upper(), "start": start, "end": end},
    )
    if not result.result_rows:
        return {"symbol": symbol.upper(), "start": start, "end": end,
                "earliest": None, "latest": None, "bar_count": 0}
    earliest, latest, n = result.result_rows[0]
    n_int = int(n) if n is not None else 0
    return {
        "symbol": symbol.upper(),
        "start": start, "end": end,
        "earliest": earliest if n_int > 0 else None,
        "latest": latest if n_int > 0 else None,
        "bar_count": n_int,
    }


async def daily_coverage_async(symbol: str, start: datetime, end: datetime) -> dict:
    return await asyncio.to_thread(daily_coverage, symbol, start, end)


def list_bars_resampled(
    symbol: str,
    interval: str,
    start: Optional[datetime],
    end: Optional[datetime],
    limit: int,
    *,
    source_table: str = "ohlcv_1m",
) -> List[dict]:
    """
    Return OHLCV bars resampled to `interval` (e.g. '1m', '5m', '1h', '1d')
    from the given `source_table` (`ohlcv_1m` or `ohlcv_5m`).

    Aggregation rules (the only correct ones for candle data):
      - open  = `argMin(open, timestamp)`  (first bar's open)
      - high  = `max(high)`
      - low   = `min(low)`
      - close = `argMax(close, timestamp)` (last bar's close)
      - volume = `sum(volume)`
    """
    if source_table not in ("ohlcv_1m", "ohlcv_5m"):
        raise ValueError(f"Unsupported source_table: {source_table!r}")
    if interval not in SUPPORTED_INTERVALS:
        raise ValueError(f"Unsupported interval: {interval!r}")
    # 1m can only come from ohlcv_1m (5m can't be downsampled).
    if interval == "1m" and source_table != "ohlcv_1m":
        raise ValueError("interval '1m' requires source_table='ohlcv_1m'")
    if interval == "1m" and start is None and end is None:
        return list_bars_desc(symbol, limit)

    where_parts = ["symbol = {sym:String}"]
    params: dict = {"sym": symbol.upper(), "lim": int(limit)}
    if start is not None:
        where_parts.append("timestamp >= {start:DateTime64(3)}")
        params["start"] = start
    if end is not None:
        where_parts.append("timestamp <= {end:DateTime64(3)}")
        params["end"] = end
    where_clause = " AND ".join(where_parts)
    interval_expr = SUPPORTED_INTERVALS[interval]

    # ORDER BY ts DESC + LIMIT keeps the NEWEST N rows; reverse in Python for
    # ascending output.
    sql = f"""
        SELECT
            toStartOfInterval(timestamp, {interval_expr}) AS bucket_ts,
            argMin(open, timestamp)  AS o,
            max(high)                AS h,
            min(low)                 AS l,
            argMax(close, timestamp) AS c,
            sum(volume)              AS v
        FROM {source_table} FINAL
        WHERE {where_clause}
        GROUP BY bucket_ts
        ORDER BY bucket_ts DESC
        LIMIT {{lim:UInt32}}
    """
    result = get_client().query(sql, parameters=params)
    out: List[dict] = []
    for row in result.result_rows:
        ts, o, h, l, c, v = row
        out.append(
            {
                "ts": ts,
                "open": float(o) if o is not None else None,
                "high": float(h) if h is not None else None,
                "low": float(l) if l is not None else None,
                "close": float(c) if c is not None else None,
                "volume": int(v) if v is not None else 0,
            }
        )
    return list(reversed(out))


def list_signals(symbol: Optional[str], limit: int) -> List[dict]:
    client = get_client()
    if symbol:
        result = client.query(
            """
            SELECT
                toString(id) AS id,
                symbol, signal_type, indicator, ts_signal,
                price_at_signal, indicator_value
            FROM signals
            WHERE symbol = {sym:String}
            ORDER BY ts_signal DESC
            LIMIT {lim:UInt32}
            """,
            parameters={"sym": symbol, "lim": limit},
        )
    else:
        result = client.query(
            """
            SELECT
                toString(id) AS id,
                symbol, signal_type, indicator, ts_signal,
                price_at_signal, indicator_value
            FROM signals
            ORDER BY ts_signal DESC
            LIMIT {lim:UInt32}
            """,
            parameters={"lim": limit},
        )
    rows = []
    for r in result.result_rows:
        rows.append(
            {
                "id": r[0],
                "symbol": r[1],
                "type": r[2],
                "indicator": r[3],
                "ts": r[4],
                "price": r[5],
                "indicator_value": r[6],
            }
        )
    return rows


def count_bars() -> int:
    r = get_client().query("SELECT count() FROM ohlcv_1m FINAL")
    return int(r.result_rows[0][0]) if r.result_rows else 0


def count_signals() -> int:
    r = get_client().query("SELECT count() FROM signals")
    return int(r.result_rows[0][0]) if r.result_rows else 0


def recent_signals(limit: int = 5) -> List[dict]:
    return list_signals(None, limit)


async def insert_bars_batch_async(rows: List[dict]) -> None:
    await asyncio.to_thread(insert_bars_batch, rows)


async def insert_signals_batch_async(rows: List[dict]) -> None:
    await asyncio.to_thread(insert_signals_batch, rows)


def latest_bar_per_symbol(symbols: List[str]) -> List[dict]:
    """Return the most recent bar for each requested symbol (ClickHouse `argMax`)."""
    if not symbols:
        return []
    client = get_client()
    result = client.query(
        """
        SELECT
            symbol,
            argMax(timestamp, timestamp) AS ts,
            argMax(open,      timestamp) AS o,
            argMax(high,      timestamp) AS h,
            argMax(low,       timestamp) AS l,
            argMax(close,     timestamp) AS c,
            argMax(volume,    timestamp) AS v,
            count() AS bar_count
        FROM ohlcv_1m FINAL
        WHERE symbol IN {syms:Array(String)}
        GROUP BY symbol
        ORDER BY symbol
        """,
        parameters={"syms": [s.upper() for s in symbols]},
    )
    out = []
    for row in result.result_rows:
        sym, ts, o, h, l, c, v, n = row
        out.append(
            {
                "symbol": sym,
                "ts": ts,
                "open": o,
                "high": h,
                "low": l,
                "close": c,
                "volume": int(v) if v is not None else 0,
                "bar_count": int(n),
            }
        )
    return out


async def latest_bar_per_symbol_async(symbols: List[str]) -> List[dict]:
    return await asyncio.to_thread(latest_bar_per_symbol, symbols)


def coverage(symbol: str, start: datetime, end: datetime) -> dict:
    """
    Return min/max timestamp and bar count for `symbol` in `[start, end]`.

    Used by the backfill service to (a) short-circuit redundant fetches and
    (b) compute the gap between requested and existing coverage.
    """
    client = get_client()
    result = client.query(
        """
        SELECT
            min(timestamp) AS earliest,
            max(timestamp) AS latest,
            count() AS bar_count
        FROM ohlcv_1m FINAL
        WHERE symbol = {sym:String}
          AND timestamp >= {start:DateTime64(3)}
          AND timestamp <= {end:DateTime64(3)}
        """,
        parameters={"sym": symbol.upper(), "start": start, "end": end},
    )
    if not result.result_rows:
        return {
            "symbol": symbol.upper(),
            "start": start,
            "end": end,
            "earliest": None,
            "latest": None,
            "bar_count": 0,
        }
    earliest, latest, n = result.result_rows[0]
    n_int = int(n) if n is not None else 0
    return {
        "symbol": symbol.upper(),
        "start": start,
        "end": end,
        "earliest": earliest if n_int > 0 else None,
        "latest": latest if n_int > 0 else None,
        "bar_count": n_int,
    }


async def coverage_async(symbol: str, start: datetime, end: datetime) -> dict:
    return await asyncio.to_thread(coverage, symbol, start, end)


def coverage_5m(symbol: str, start: datetime, end: datetime) -> dict:
    """Coverage report against `ohlcv_5m`. Same shape as `coverage()`."""
    result = get_client().query(
        """
        SELECT min(timestamp), max(timestamp), count()
        FROM ohlcv_5m FINAL
        WHERE symbol = {sym:String}
          AND timestamp >= {start:DateTime64(3)}
          AND timestamp <= {end:DateTime64(3)}
        """,
        parameters={"sym": symbol.upper(), "start": start, "end": end},
    )
    if not result.result_rows:
        return {"symbol": symbol.upper(), "start": start, "end": end,
                "earliest": None, "latest": None, "bar_count": 0}
    earliest, latest, n = result.result_rows[0]
    n_int = int(n) if n is not None else 0
    return {
        "symbol": symbol.upper(),
        "start": start, "end": end,
        "earliest": earliest if n_int > 0 else None,
        "latest": latest if n_int > 0 else None,
        "bar_count": n_int,
    }


async def coverage_5m_async(symbol: str, start: datetime, end: datetime) -> dict:
    return await asyncio.to_thread(coverage_5m, symbol, start, end)


# ---------- Gap detection ----------

# Source-table → bar resolution in minutes. Used by `find_intraday_gaps`.
_TABLE_STEP_MINUTES = {"ohlcv_1m": 1, "ohlcv_5m": 5}


def find_intraday_gaps(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    source_table: str = "ohlcv_1m",
    session_boundary_minutes: int = 4 * 60,
    max_results: int = 5000,
) -> list[dict]:
    """
    Return within-session gaps in `[start, end]` for the given symbol/source.

    A "gap" is two consecutive stored bars whose timestamp delta exceeds the
    table's bar resolution but is SMALLER than `session_boundary_minutes`
    (default 4h). Gaps larger than that are treated as overnight/weekend
    boundaries and ignored, mirroring the frontend chart logic.

    Each returned record has:
        prev_ts: timestamp of the bar BEFORE the gap (UTC-aware)
        next_ts: timestamp of the bar AFTER the gap (UTC-aware)
        missing: integer count of missing bars between them at the source's
                 native resolution.
    Results are returned chronologically (oldest first). When the total number
    of gaps exceeds `max_results`, the **newest** gaps are kept — fresh holes
    (the ones the user just saw, and which a live or delayed provider can
    still produce) are always prioritized over historical noise that's been
    sitting unfilled for weeks.
    """
    if source_table not in _TABLE_STEP_MINUTES:
        raise ValueError(f"Unsupported source_table: {source_table!r}")
    step_min = _TABLE_STEP_MINUTES[source_table]

    # Use `lagInFrame` (proper SQL window function). The older `neighbor()` is
    # deprecated in ClickHouse >= 24.x because it's order-of-evaluation-dependent.
    # `lagInFrame` returns NULL for the first row, which we filter out.
    #
    # The inner LIMIT orders DESC so the **most recent** gaps survive
    # truncation; we re-sort to ascending in Python so the public contract
    # ("ordered chronologically") and the gap-range merger downstream stay
    # correct. Prior to this fix, the LIMIT kept the OLDEST gaps and silently
    # dropped today's holes for any symbol that had accumulated more than
    # `max_results` historical gaps (e.g. from a previous provider's session
    # boundaries) — which made manual "Fill gaps" a no-op for the windows
    # users actually care about.
    sql = f"""
        SELECT prev_ts, ts, dateDiff('minute', prev_ts, ts) AS delta_min
        FROM (
            SELECT
                timestamp AS ts,
                lagInFrame(timestamp, 1) OVER (ORDER BY timestamp ASC) AS prev_ts
            FROM {source_table} FINAL
            WHERE symbol = {{sym:String}}
              AND timestamp >= {{start:DateTime64(3)}}
              AND timestamp <= {{end:DateTime64(3)}}
        )
        WHERE prev_ts != toDateTime64(0, 3, 'UTC')
          AND dateDiff('minute', prev_ts, ts) > {{step:UInt32}}
          AND dateDiff('minute', prev_ts, ts) < {{boundary:UInt32}}
        ORDER BY prev_ts DESC
        LIMIT {{lim:UInt32}}
    """
    result = get_client().query(
        sql,
        parameters={
            "sym": symbol.upper(),
            "start": start,
            "end": end,
            "step": step_min,
            "boundary": session_boundary_minutes,
            "lim": int(max_results),
        },
    )
    out: list[dict] = []
    for prev_ts, ts, delta in result.result_rows:
        # ClickHouse returns naive DateTime; force-UTC for consumers.
        if prev_ts.tzinfo is None:
            prev_ts = prev_ts.replace(tzinfo=timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        missing = max(1, int(delta) // step_min - 1)
        out.append({"prev_ts": prev_ts, "next_ts": ts, "missing": missing})
    # Re-sort to chronological order so downstream consumers (the gap-range
    # merger, the routes_backfill response, and existing tests) keep their
    # "oldest first" contract.
    out.sort(key=lambda g: g["prev_ts"])
    return out


async def find_intraday_gaps_async(
    symbol: str,
    start: datetime,
    end: datetime,
    *,
    source_table: str = "ohlcv_1m",
    session_boundary_minutes: int = 4 * 60,
    max_results: int = 5000,
) -> list[dict]:
    return await asyncio.to_thread(
        find_intraday_gaps,
        symbol, start, end,
        source_table=source_table,
        session_boundary_minutes=session_boundary_minutes,
        max_results=max_results,
    )
