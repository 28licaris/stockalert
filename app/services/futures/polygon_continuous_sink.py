"""Iceberg sink for futures.polygon_continuous (derived continuous roots).

Writes the volume-rolled, ratio-back-adjusted continuous series produced by
scripts/polygon_futures_build_continuous.py. Analog of the equities adjusted
write path. Append-only; (symbol, timestamp) is the identifier.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

import pyarrow as pa

from app.services.futures.tables import (
    POLYGON_CONTINUOUS_TABLE_NAME,
    ensure_polygon_continuous,
)
from app.services.iceberg_catalog import get_catalog

logger = logging.getLogger(__name__)

_ARROW = pa.schema([
    pa.field("symbol",          pa.string(),                  nullable=False),
    pa.field("timestamp",       pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("open",            pa.float64(),                 nullable=True),
    pa.field("high",            pa.float64(),                 nullable=True),
    pa.field("low",             pa.float64(),                 nullable=True),
    pa.field("close",           pa.float64(),                 nullable=True),
    pa.field("volume",          pa.float64(),                 nullable=True),
    pa.field("vwap",            pa.float64(),                 nullable=True),
    pa.field("trade_count",     pa.int64(),                   nullable=True),
    pa.field("adj_factor",      pa.float64(),                 nullable=True),
    pa.field("contract",        pa.string(),                  nullable=True),
    pa.field("source",          pa.string(),                  nullable=True),
    pa.field("ingestion_ts",    pa.timestamp("us", tz="UTC"), nullable=True),
    pa.field("ingestion_run_id", pa.string(),                 nullable=True),
])


class PolygonContinuousSink:
    """Synchronous Iceberg writer for futures.polygon_continuous."""

    def __init__(self) -> None:
        self._table = ensure_polygon_continuous(get_catalog())
        self._run_id = str(uuid.uuid4())
        self._ingestion_ts = datetime.now(tz=timezone.utc).replace(microsecond=0)

    @property
    def table_name(self) -> str:
        return f"futures.{POLYGON_CONTINUOUS_TABLE_NAME}"

    def _build_arrow(self, df):
        n = len(df)
        ts = pa.array(df["timestamp"]).cast(pa.timestamp("us", tz="UTC"))
        return pa.table({
            "symbol":           pa.array(df["symbol"].astype("string"),   type=pa.string()),
            "timestamp":        ts,
            "open":             pa.array(df["open"],        type=pa.float64()),
            "high":             pa.array(df["high"],        type=pa.float64()),
            "low":              pa.array(df["low"],         type=pa.float64()),
            "close":            pa.array(df["close"],       type=pa.float64()),
            "volume":           pa.array(df["volume"],      type=pa.float64()),
            "vwap":             pa.array(df["vwap"],        type=pa.float64()),
            "trade_count":      pa.array(df["trade_count"], type=pa.int64()),
            "adj_factor":       pa.array(df["adj_factor"],  type=pa.float64()),
            "contract":         pa.array(df["contract"].astype("string"), type=pa.string()),
            "source":           pa.array(["polygon-continuous-vroll"] * n, type=pa.string()),
            "ingestion_ts":     pa.array([self._ingestion_ts] * n, type=pa.timestamp("us", tz="UTC")),
            "ingestion_run_id": pa.array([self._run_id] * n,       type=pa.string()),
        }, schema=_ARROW)

    def write_frame(self, df) -> int:
        """Append a built continuous DataFrame. Columns: symbol, timestamp,
        open, high, low, close, volume, vwap, trade_count, adj_factor,
        contract. Raises on append failure."""
        if df is None or len(df) == 0:
            return 0
        self._table.append(self._build_arrow(df))
        return len(df)

    def replace_symbol(self, df, symbol: str) -> int:
        """Atomically replace ALL rows for one continuous root (overwrite by
        symbol filter, single commit). Used by the nightly rebuild so back-
        adjustment re-scaling at rolls is applied without dropping the live
        table. Other roots stay queryable throughout."""
        from pyiceberg.expressions import EqualTo

        if df is None or len(df) == 0:
            return 0
        self._table.overwrite(self._build_arrow(df),
                              overwrite_filter=EqualTo("symbol", symbol))
        return len(df)
