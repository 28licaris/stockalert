"""Iceberg sink for futures.polygon_raw (per-contract raw 1-min OHLCV).

Synchronous Iceberg writer for the `polygon_raw` table — outright contracts
(ESH4, CLM4, …) parsed verbatim from the flat-file mirror, no roll, no
adjustment. Analog of `equities.polygon_raw`.

Populated by scripts/polygon_futures_parse_raw.py (and the nightly futures
refresh once wired in). Idempotent: (contract, timestamp) is the Iceberg
identifier, so dedup happens on read.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import pyarrow as pa

from app.services.futures.tables import POLYGON_RAW_TABLE_NAME, ensure_polygon_raw
from app.services.iceberg_catalog import get_catalog

logger = logging.getLogger(__name__)

_POLYGON_RAW_ARROW = pa.schema([
    pa.field("contract",        pa.string(),                  nullable=False),
    pa.field("timestamp",       pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("open",            pa.float64(),                 nullable=True),
    pa.field("high",            pa.float64(),                 nullable=True),
    pa.field("low",             pa.float64(),                 nullable=True),
    pa.field("close",           pa.float64(),                 nullable=True),
    pa.field("volume",          pa.float64(),                 nullable=True),
    pa.field("vwap",            pa.float64(),                 nullable=True),
    pa.field("trade_count",     pa.int64(),                   nullable=True),
    pa.field("dollar_volume",   pa.float64(),                 nullable=True),
    pa.field("root",            pa.string(),                  nullable=True),
    pa.field("exchange",        pa.string(),                  nullable=True),
    pa.field("source",          pa.string(),                  nullable=True),
    pa.field("ingestion_ts",    pa.timestamp("us", tz="UTC"), nullable=True),
    pa.field("ingestion_run_id", pa.string(),                 nullable=True),
])


class PolygonRawFuturesSink:
    """Synchronous Iceberg writer for futures.polygon_raw.

    Batch/backfill use — one Arrow `table.append()` per call (atomic at the
    Iceberg file level). Append-only: physical dedup is deferred (read-side via
    the (contract, timestamp) identifier), matching the bronze-appends pattern.
    """

    def __init__(self) -> None:
        self._table = ensure_polygon_raw(get_catalog())
        self._run_id = str(uuid.uuid4())
        self._ingestion_ts = datetime.now(tz=timezone.utc).replace(microsecond=0)

    @property
    def table_name(self) -> str:
        return f"futures.{POLYGON_RAW_TABLE_NAME}"

    def write_frame(self, df) -> int:
        """Append a parsed DataFrame (vectorized — for bulk parse at scale).

        Expected columns: contract, timestamp (datetime64 UTC), open, high,
        low, close, volume, vwap, trade_count, dollar_volume, root, exchange.
        Stamps source/ingestion_ts/ingestion_run_id. Raises on append failure.
        """
        if df is None or len(df) == 0:
            return 0
        n = len(df)
        ts = pa.array(df["timestamp"]).cast(pa.timestamp("us", tz="UTC"))
        arrow = pa.table({
            "contract":         pa.array(df["contract"].astype("string"),       type=pa.string()),
            "timestamp":        ts,
            "open":             pa.array(df["open"],          type=pa.float64()),
            "high":             pa.array(df["high"],          type=pa.float64()),
            "low":              pa.array(df["low"],           type=pa.float64()),
            "close":            pa.array(df["close"],         type=pa.float64()),
            "volume":           pa.array(df["volume"],        type=pa.float64()),
            "vwap":             pa.array(df["vwap"],          type=pa.float64()),
            "trade_count":      pa.array(df["trade_count"],   type=pa.int64()),
            "dollar_volume":    pa.array(df["dollar_volume"], type=pa.float64()),
            "root":             pa.array(df["root"].astype("string"),     type=pa.string()),
            "exchange":         pa.array(df["exchange"].astype("string"), type=pa.string()),
            "source":           pa.array(["polygon-flatfile-mirror"] * n, type=pa.string()),
            "ingestion_ts":     pa.array([self._ingestion_ts] * n,        type=pa.timestamp("us", tz="UTC")),
            "ingestion_run_id": pa.array([self._run_id] * n,              type=pa.string()),
        }, schema=_POLYGON_RAW_ARROW)
        self._table.append(arrow)
        return n

    def write_batch(self, rows: list[dict]) -> int:
        """Append rows to futures.polygon_raw. Returns rows written.

        Each dict needs: contract, timestamp (UTC datetime), root; OHLCV +
        vwap/trade_count/dollar_volume/exchange optional. Raises on append
        failure (callers in a bulk parse must NOT silently continue past a
        write error — they need an accurate reconcile)."""
        if not rows:
            return 0
        valid = [r for r in rows if r.get("contract") and r.get("timestamp") is not None]
        if not valid:
            logger.warning("polygon_raw_sink: all %d rows failed validation", len(rows))
            return 0
        arrow = _to_arrow(valid, self._run_id, self._ingestion_ts)
        self._table.append(arrow)
        logger.debug("polygon_raw_sink: appended %d rows", len(valid))
        return len(valid)


def _to_arrow(rows: list[dict], run_id: str, ingestion_ts: datetime) -> pa.Table:
    n = len(rows)
    return pa.table({
        "contract":         pa.array([r["contract"] for r in rows],            type=pa.string()),
        "timestamp":        pa.array([r["timestamp"] for r in rows],           type=pa.timestamp("us", tz="UTC")),
        "open":             pa.array([_f(r.get("open")) for r in rows],        type=pa.float64()),
        "high":             pa.array([_f(r.get("high")) for r in rows],        type=pa.float64()),
        "low":              pa.array([_f(r.get("low")) for r in rows],         type=pa.float64()),
        "close":            pa.array([_f(r.get("close")) for r in rows],       type=pa.float64()),
        "volume":           pa.array([_f(r.get("volume")) for r in rows],      type=pa.float64()),
        "vwap":             pa.array([_f(r.get("vwap")) for r in rows],        type=pa.float64()),
        "trade_count":      pa.array([_i(r.get("trade_count")) for r in rows], type=pa.int64()),
        "dollar_volume":    pa.array([_f(r.get("dollar_volume")) for r in rows], type=pa.float64()),
        "root":             pa.array([r.get("root") for r in rows],            type=pa.string()),
        "exchange":         pa.array([r.get("exchange") for r in rows],        type=pa.string()),
        "source":           pa.array(["polygon-flatfile-mirror"] * n,         type=pa.string()),
        "ingestion_ts":     pa.array([ingestion_ts] * n,                       type=pa.timestamp("us", tz="UTC")),
        "ingestion_run_id": pa.array([run_id] * n,                             type=pa.string()),
    }, schema=_POLYGON_RAW_ARROW)


def _f(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return None if f != f else f  # drop NaN; keep 0.0 (valid for raw)
    except (TypeError, ValueError):
        return None


def _i(v) -> Optional[int]:
    if v is None:
        return None
    try:
        if v != v:  # NaN
            return None
        return int(v)
    except (TypeError, ValueError):
        return None
