"""
Sinks for the ingest pipeline.

The flat-files backfill service downloads one day's worth of bars,
canonicalises the DataFrame, and hands it to a list of ``Sink`` objects.
Each sink is independent — it owns its own success criteria, its own
failure semantics, and its own storage. Adding a new destination (e.g.
DuckDB, Snowflake, Parquet-on-local-disk for dev) is "implement Sink".

Two production-friendly sinks live here today:

  - ``ClickHouseSink``   — writes to the hot cache (``ohlcv_1m`` / ``ohlcv_daily``)
  - ``BronzeIcebergSink`` — see ``app.services.bronze.sink`` (the canonical
    cold-tier writer; lives in the bronze service so it can own its
    schemas alongside)

A third, **legacy** sink (``LakeSink`` → ``raw/provider=*/...parquet``) lives
in ``app.services.legacy.lake_sink``. Do not use it for new work; it is
slated for removal in Phase 6.

Design rules:

  1. Sinks consume the **same canonical DataFrame**. The canonical shape
     is defined in ``app.services.ingest.flatfiles_backfill._canonicalize_frame``
     (single source of truth) so the writes are guaranteed consistent —
     if both succeed, both stores hold the same bytes' worth of bars.
  2. Sinks are **independent**. A sink's failure does NOT stop the next
     sink from running. The caller aggregates per-sink ``SinkResult`` s
     into the day's ``DayResult.status``.
  3. Sinks are **idempotent**. Re-running a sink on the same day is a
     no-op or an overwrite, not a duplicate. ClickHouse provides this
     via ``ReplacingMergeTree(version)``; bronze provides this via the
     upstream watermark + append model (see bronze service README).
  4. Sinks are **microservice-ready**. No singletons, no FastAPI imports,
     no implicit ``app.config`` access in the hot path. Construction is
     explicit; ``from_settings()`` factories on each sink are the *only*
     point where the global settings touch the code path.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Awaitable, Callable, List, Literal, Optional, Protocol

import pandas as pd

logger = logging.getLogger(__name__)


Kind = Literal["minute", "day"]


# Async insert function signature used by both 1m and daily ClickHouse paths.
InsertFn = Callable[[List[dict]], Awaitable[None]]


@dataclass(slots=True)
class SinkResult:
    """Per-sink outcome for one (date, kind, provider) write.

    Aggregated into ``DayResult.sink_results`` by the backfill service.
    Sinks always *return* a result (never raise) so a single sink's
    explosion can't poison the run; severity is encoded in ``status``.
    """
    sink: str            # human-readable name (e.g. "clickhouse", "bronze_iceberg")
    status: str          # "ok" | "skipped" | "error"
    bars_written: int = 0
    error: Optional[str] = None
    metadata: dict = field(default_factory=dict)


class Sink(Protocol):
    """Anything that can absorb a canonical day's bars.

    Implementations should be:
      - Async at the public boundary (``write`` returns ``Awaitable``)
      - Idempotent on the same (date, kind, provider, frame) tuple
      - Self-classifying: they return ``SinkResult`` with ``status``
        rather than raising — except for genuinely catastrophic errors
        that warrant aborting the whole range

    Custom sinks need only implement ``name`` and ``write``. They do NOT
    need to inherit from anything (Protocol = structural typing).
    """
    name: str

    async def write(
        self,
        df: pd.DataFrame,
        *,
        file_date: date,
        kind: Kind,
        provider: str,
    ) -> SinkResult:
        """Persist ``df`` for one (date, kind, provider) tuple.

        ``df`` is the canonical frame produced by the backfill service.
        Sinks MUST NOT mutate it (a single frame is fanned out to N
        sinks; in-place mutation would corrupt later writes).
        """
        ...


# ---------- ClickHouseSink ----------


class ClickHouseSink:
    """
    Hot-cache sink. Writes canonical bars to ``ohlcv_1m`` (minute) or
    ``ohlcv_daily`` (day) via the existing async insert functions.

    Idempotency is provided by the ClickHouse engine
    (``ReplacingMergeTree(version)`` on ``(symbol, timestamp)``) — re-running
    the same day yields a higher version that supersedes the old row.
    """
    DEFAULT_BATCH_SIZE = 1000
    name = "clickhouse"

    def __init__(
        self,
        *,
        insert_minute_fn: InsertFn,
        insert_daily_fn: InsertFn,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        if insert_minute_fn is None or insert_daily_fn is None:
            raise ValueError(
                "ClickHouseSink: insert_minute_fn and insert_daily_fn are required"
            )
        self._insert_minute_fn = insert_minute_fn
        self._insert_daily_fn = insert_daily_fn
        self._batch_size = max(1, int(batch_size))

    @classmethod
    def from_settings(cls, *, batch_size: int = DEFAULT_BATCH_SIZE) -> "ClickHouseSink":
        # Lazy import keeps this module decoupled from ``app.db`` at
        # import time (matters for the future microservice split).
        from app.db.queries import (
            insert_bars_batch_async,
            insert_daily_bars_batch_async,
        )
        return cls(
            insert_minute_fn=insert_bars_batch_async,
            insert_daily_fn=insert_daily_bars_batch_async,
            batch_size=batch_size,
        )

    @property
    def batch_size(self) -> int:
        return self._batch_size

    async def write(
        self,
        df: pd.DataFrame,
        *,
        file_date: date,
        kind: Kind,
        provider: str,
    ) -> SinkResult:
        if df is None or df.empty:
            return SinkResult(
                sink=self.name, status="skipped", bars_written=0,
                metadata={"reason": "empty_frame"},
            )
        try:
            records = _frame_to_records(df, kind=kind)
            if not records:
                return SinkResult(
                    sink=self.name, status="skipped", bars_written=0,
                    metadata={"reason": "no_valid_rows"},
                )
            batches = await self._insert_batched(records, kind=kind)
            return SinkResult(
                sink=self.name, status="ok",
                bars_written=len(records),
                metadata={"batches": batches},
            )
        except Exception as e:
            logger.exception(
                "clickhouse_sink: insert failed for %s %s: %s",
                kind, file_date, e,
            )
            return SinkResult(
                sink=self.name, status="error", bars_written=0,
                error=str(e),
            )

    async def _insert_batched(self, records: List[dict], *, kind: Kind) -> int:
        """Insert ``records`` in fixed-size batches. Returns the number
        of batches sent (useful in telemetry)."""
        insert_fn = (
            self._insert_minute_fn if kind == "minute" else self._insert_daily_fn
        )
        n = len(records)
        batches = 0
        for i in range(0, n, self._batch_size):
            await insert_fn(records[i : i + self._batch_size])
            batches += 1
        return batches


# ---------- canonical record conversion ----------


def _frame_to_records(df: pd.DataFrame, *, kind: Kind) -> List[dict]:
    """
    Convert a canonical-shape DataFrame back into ClickHouse-friendly
    ``list[dict]`` rows. Splits the work from the sink itself so the
    canonicalisation logic is reusable (and trivially testable).

    Defensive against NaN values: rows missing any of ``open / high /
    low / close / volume / timestamp`` are dropped (not silently
    poisoning the batch).
    """
    if df is None or df.empty:
        return []
    if kind == "minute":
        cols = ("symbol", "timestamp", "open", "high", "low", "close",
                "volume", "vwap", "trade_count", "source")
    else:
        cols = ("symbol", "timestamp", "open", "high", "low", "close",
                "volume", "source")
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise ValueError(
            f"_frame_to_records: canonical frame missing columns: {missing}"
        )
    records: List[dict] = []
    columns = df.loc[:, list(cols)]
    for row in columns.itertuples(index=False, name=None):
        if kind == "minute":
            sym, ts, o, h, lo, c, v, vw, tc, src = row
            if pd.isna(ts) or any(pd.isna(x) for x in (o, h, lo, c, v)):
                continue
            records.append({
                "symbol": str(sym),
                "timestamp": ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                "open": float(o),
                "high": float(h),
                "low": float(lo),
                "close": float(c),
                "volume": float(v),
                "vwap": float(vw) if not pd.isna(vw) else 0.0,
                "trade_count": int(tc) if not pd.isna(tc) else 0,
                "source": str(src),
            })
        else:
            sym, ts, o, h, lo, c, v, src = row
            if pd.isna(ts) or any(pd.isna(x) for x in (o, h, lo, c, v)):
                continue
            records.append({
                "symbol": str(sym),
                "timestamp": ts.to_pydatetime() if hasattr(ts, "to_pydatetime") else ts,
                "open": float(o),
                "high": float(h),
                "low": float(lo),
                "close": float(c),
                "volume": float(v),
                "source": str(src),
            })
    return records


__all__ = [
    "ClickHouseSink",
    "InsertFn",
    "Kind",
    "Sink",
    "SinkResult",
    "_frame_to_records",
]
