"""Iceberg sink for futures.polygon_futures (Polygon deep-history 1-min OHLCV).

Mirrors schwab_sink.py but targets the `polygon_futures` table which holds
bars pulled from Polygon's per-contract futures aggregates API and stitched
into continuous roots (/ES, /NQ, …).

Populated exclusively by scripts/polygon_futures_backfill.py (and the
optional nightly Polygon futures refresh once wired in). Never written by
the live stream path.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

import pyarrow as pa

from app.services.futures.tables import (
    POLYGON_FUTURES_TABLE_NAME,
    ensure_polygon_futures,
)
from app.services.iceberg_catalog import get_catalog

logger = logging.getLogger(__name__)

# Same column layout as schwab_futures — no adj_factor for futures.
_POLYGON_FUTURES_ARROW = pa.schema([
    pa.field("symbol",          pa.string(),                  nullable=False),
    pa.field("timestamp",       pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("open",            pa.float64(),                 nullable=True),
    pa.field("high",            pa.float64(),                 nullable=True),
    pa.field("low",             pa.float64(),                 nullable=True),
    pa.field("close",           pa.float64(),                 nullable=True),
    pa.field("volume",          pa.float64(),                 nullable=True),
    pa.field("vwap",            pa.float64(),                 nullable=True),
    pa.field("trade_count",     pa.int64(),                   nullable=True),
    pa.field("source",          pa.string(),                  nullable=True),
    pa.field("ingestion_ts",    pa.timestamp("us", tz="UTC"), nullable=True),
    pa.field("ingestion_run_id", pa.string(),                 nullable=True),
])


class PolygonFuturesSink:
    """Synchronous Iceberg writer for futures.polygon_futures.

    Designed for batch / backfill use — writes one Arrow batch per call.
    Thread-safe: each write is a single `table.append()` which is atomic
    at the Iceberg file level. Idempotent re-runs are safe because
    (symbol, timestamp) is the Iceberg identifier and CH's
    ReplacingMergeTree dedupes on read.

    Usage:
        sink = PolygonFuturesSink()
        rows_written = sink.write_batch(bars)  # bars: list[dict]
    """

    def __init__(self) -> None:
        self._table = ensure_polygon_futures(get_catalog())
        self._run_id = str(uuid.uuid4())
        self._ingestion_ts = datetime.now(tz=timezone.utc).replace(microsecond=0)

    @property
    def table_name(self) -> str:
        return f"futures.{POLYGON_FUTURES_TABLE_NAME}"

    def write_batch(self, bars: list[dict]) -> int:
        """Append a list of bar dicts to futures.polygon_futures.

        Each dict must have: symbol, timestamp (datetime, UTC), open, high,
        low, close, volume. vwap and trade_count are optional.

        Returns the number of rows written (0 if bars is empty). Never raises
        — logs errors and returns 0 so the caller's loop continues.
        """
        if not bars:
            return 0

        valid: list[dict] = []
        for b in bars:
            if not b.get("symbol") or b.get("timestamp") is None:
                continue
            valid.append(b)

        if not valid:
            logger.warning("polygon_futures_sink: all %d bars failed validation", len(bars))
            return 0

        try:
            arrow = _to_arrow(valid, self._run_id, self._ingestion_ts)
        except Exception as exc:
            logger.error("polygon_futures_sink: Arrow conversion failed: %s", exc)
            return 0

        try:
            self._table.append(arrow)
        except Exception as exc:
            logger.error(
                "polygon_futures_sink: Iceberg append failed (%d rows): %s",
                len(valid), exc,
            )
            return 0

        logger.debug("polygon_futures_sink: appended %d rows", len(valid))
        return len(valid)

    def refresh_snapshot(self) -> Optional[int]:
        """Refresh the table handle and return the current snapshot id."""
        try:
            self._table.refresh()
            snap = self._table.current_snapshot()
            return snap.snapshot_id if snap else None
        except Exception:
            return None


def _to_arrow(
    bars: list[dict],
    run_id: str,
    ingestion_ts: datetime,
) -> pa.Table:
    """Convert a list of bar dicts to a typed PyArrow table."""
    n = len(bars)
    arrays = {
        "symbol":           pa.array([b["symbol"] for b in bars],       type=pa.string()),
        "timestamp":        pa.array([b["timestamp"] for b in bars],     type=pa.timestamp("us", tz="UTC")),
        "open":             pa.array([_f(b.get("open")) for b in bars],  type=pa.float64()),
        "high":             pa.array([_f(b.get("high")) for b in bars],  type=pa.float64()),
        "low":              pa.array([_f(b.get("low")) for b in bars],   type=pa.float64()),
        "close":            pa.array([_f(b.get("close")) for b in bars], type=pa.float64()),
        "volume":           pa.array([_f(b.get("volume")) for b in bars],type=pa.float64()),
        "vwap":             pa.array([_f(b.get("vwap")) for b in bars],  type=pa.float64()),
        "trade_count":      pa.array([_i(b.get("trade_count")) for b in bars], type=pa.int64()),
        "source":           pa.array(["polygon-futures"] * n,            type=pa.string()),
        "ingestion_ts":     pa.array([ingestion_ts] * n,                 type=pa.timestamp("us", tz="UTC")),
        "ingestion_run_id": pa.array([run_id] * n,                       type=pa.string()),
    }
    return pa.table(arrays, schema=_POLYGON_FUTURES_ARROW)


def _f(v) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return None if f == 0.0 else f
    except (TypeError, ValueError):
        return None


def _i(v) -> Optional[int]:
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
