"""Typed ClickHouse reads/writes for OHLCV and signals."""
from __future__ import annotations

import asyncio
import time
import uuid
from datetime import datetime
from typing import Any, List, Optional

import pandas as pd

from app.db.client import get_client


def _now_version() -> int:
    return time.time_ns() // 1_000_000


def insert_bars_batch(rows: List[dict]) -> None:
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
    client.insert(
        "ohlcv_1m",
        data,
        column_names=[
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
        ],
    )


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
