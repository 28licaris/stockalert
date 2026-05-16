"""
BronzeIcebergSink — the canonical writer for `bronze.{provider}_{kind}`
Iceberg tables. Drop-in replacement for the legacy `LakeSink`
in the fan-out pattern from `app.services.flatfiles_sinks`.

Implements the `Sink` Protocol:
  - Consumes the same canonical DataFrame as `ClickHouseSink` / `LakeSink`.
  - Async at the boundary, idempotent on re-runs.
  - Returns `SinkResult` rather than raising on expected failures.

One sink class, many tables. The sink is **table-agnostic** —
construct it with the target Iceberg table + the set of
`(provider, kind)` tuples it accepts. Use the factory classmethods
(`for_polygon_minute()`, `for_schwab_minute()`) for the common cases.

Idempotency:
  Writes use `append`, not `overwrite`. PyIceberg's `overwrite(filter)`
  requires reading existing files to determine which rows to delete —
  even with partition pruning to one month, that's hundreds of MB of
  I/O per write, unacceptable for a live or nightly cadence.

  Instead, idempotency is enforced UPSTREAM by the callers:
    - Nightly archive: a watermark in CH `lake_archive_watermarks`
      (becoming `ingestion_runs`) short-circuits before this sink is
      called for a day already written.
    - Future live writer: a "last-flushed-ts" cursor so only rows
      strictly newer than the last successful flush get appended.

  If a duplicate write ever does occur (operator forces a re-run with
  --force, for example), silver's provider-precedence + argMax-style
  dedup handles it during silver-build. Bronze stores "what we got",
  not "what's canonical".

Data-quality boundary: rows missing `symbol` or `timestamp` are dropped
before write (matches the filter we applied to the historical Athena
import; see decision log 2026-05-14).
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Optional

import pandas as pd
import pyarrow as pa
from pyiceberg.table import Table

from app.services.bronze.tables import (
    ensure_bronze_polygon_minute,
    ensure_bronze_schwab_minute,
)
from app.services.flatfiles_sinks import Kind, SinkResult
from app.services.iceberg_catalog import get_catalog

logger = logging.getLogger(__name__)


# Target Arrow schema for the canonical bronze.*_minute shape. Used for
# every minute-bar bronze table — polygon, schwab, alpaca all share it.
_BRONZE_MINUTE_ARROW = pa.schema(
    [
        pa.field("symbol", pa.string(), nullable=False),
        pa.field("timestamp", pa.timestamp("us", tz="UTC"), nullable=False),
        pa.field("open", pa.float64(), nullable=True),
        pa.field("high", pa.float64(), nullable=True),
        pa.field("low", pa.float64(), nullable=True),
        pa.field("close", pa.float64(), nullable=True),
        pa.field("volume", pa.float64(), nullable=True),
        pa.field("vwap", pa.float64(), nullable=True),
        pa.field("trade_count", pa.int64(), nullable=True),
        pa.field("source", pa.string(), nullable=True),
        pa.field("ingestion_ts", pa.timestamp("us", tz="UTC"), nullable=True),
        pa.field("ingestion_run_id", pa.string(), nullable=True),
    ]
)


@dataclass(slots=True)
class _PreparedFrame:
    arrow: pa.Table
    rows_in: int
    rows_dropped_null_symbol: int
    rows_dropped_null_ts: int


def _prepare_frame(
    df: pd.DataFrame,
    *,
    ingestion_run_id: str,
    ingestion_ts: datetime,
) -> _PreparedFrame:
    """
    Project the canonical-shape DataFrame down to the bronze schema and
    cast/clean types so PyIceberg can write it without complaint.

    - Drops rows with NULL symbol or timestamp (matches the
      data-quality boundary used in the historical Athena import).
    - Converts vwap == 0.0 to NULL (Polygon flat-files use 0.0 as a
      placeholder; future writers emit NULL directly).
    - Stamps ingestion_ts and ingestion_run_id on every row.
    """
    rows_in = len(df)

    keep = ["symbol", "timestamp", "open", "high", "low", "close",
            "volume", "vwap", "trade_count", "source"]
    missing = [c for c in keep if c not in df.columns]
    if missing:
        raise ValueError(
            f"BronzeIcebergSink: input frame missing required columns: {missing}"
        )

    work = df.loc[:, keep].copy()

    null_sym = work["symbol"].isna() | (work["symbol"].astype(str).str.len() == 0)
    null_ts = work["timestamp"].isna()
    rows_dropped_null_symbol = int(null_sym.sum())
    rows_dropped_null_ts = int(null_ts.sum())
    work = work.loc[~(null_sym | null_ts)]

    if "vwap" in work.columns:
        work.loc[work["vwap"] == 0.0, "vwap"] = pd.NA

    work["ingestion_ts"] = ingestion_ts
    work["ingestion_run_id"] = ingestion_run_id

    arrow = pa.Table.from_pandas(work, schema=_BRONZE_MINUTE_ARROW, preserve_index=False)

    return _PreparedFrame(
        arrow=arrow,
        rows_in=rows_in,
        rows_dropped_null_symbol=rows_dropped_null_symbol,
        rows_dropped_null_ts=rows_dropped_null_ts,
    )


class BronzeIcebergSink:
    """
    Iceberg sink for any bronze table that shares the canonical
    12-column minute-bar schema.

    Construct via:
      - `BronzeIcebergSink.for_polygon_minute()`
      - `BronzeIcebergSink.for_schwab_minute()`

    Or directly with `BronzeIcebergSink(table=..., name=...,
    accepted_providers={...})` for one-off / test use.
    """

    def __init__(
        self,
        *,
        table: Table,
        name: str = "bronze_iceberg",
        accepted_providers: set[tuple[str, str]] | None = None,
    ) -> None:
        if table is None:
            raise ValueError("BronzeIcebergSink: table is required")
        self._table = table
        self._name = name
        # None = accept any provider/kind (useful for test fixtures).
        self._accepted_providers = accepted_providers

    @property
    def name(self) -> str:
        return self._name

    @property
    def table(self) -> Table:
        return self._table

    @classmethod
    def for_polygon_minute(cls) -> "BronzeIcebergSink":
        catalog = get_catalog()
        table = ensure_bronze_polygon_minute(catalog)
        return cls(
            table=table,
            name="bronze_polygon_minute",
            accepted_providers={
                ("polygon", "minute"),
                ("polygon-flatfiles", "minute"),
            },
        )

    @classmethod
    def for_schwab_minute(cls) -> "BronzeIcebergSink":
        catalog = get_catalog()
        table = ensure_bronze_schwab_minute(catalog)
        return cls(
            table=table,
            name="bronze_schwab_minute",
            accepted_providers={
                ("schwab", "minute"),
                ("schwab-rest", "minute"),
            },
        )

    @classmethod
    def from_settings(cls) -> "BronzeIcebergSink":
        """Backward-compatible alias. Defaults to polygon_minute."""
        return cls.for_polygon_minute()

    async def write(
        self,
        df: pd.DataFrame,
        *,
        file_date: date,
        kind: Kind,
        provider: str,
    ) -> SinkResult:
        if (
            self._accepted_providers is not None
            and (provider, kind) not in self._accepted_providers
        ):
            return SinkResult(
                sink=self.name,
                status="skipped",
                bars_written=0,
                metadata={"reason": f"unsupported (provider={provider}, kind={kind})"},
            )

        if df is None or df.empty:
            return SinkResult(
                sink=self.name, status="skipped", bars_written=0,
                metadata={"reason": "empty_frame"},
            )

        ingestion_run_id = str(uuid.uuid4())
        ingestion_ts = datetime.now(tz=timezone.utc).replace(microsecond=0)

        try:
            prepared = _prepare_frame(
                df,
                ingestion_run_id=ingestion_run_id,
                ingestion_ts=ingestion_ts,
            )
        except Exception as e:
            logger.exception("bronze_iceberg_sink: prepare failed for %s: %s", file_date, e)
            return SinkResult(
                sink=self.name, status="error", bars_written=0, error=str(e),
            )

        if prepared.arrow.num_rows == 0:
            return SinkResult(
                sink=self.name, status="skipped", bars_written=0,
                metadata={
                    "reason": "no_valid_rows_after_filter",
                    "rows_in": prepared.rows_in,
                    "rows_dropped_null_symbol": prepared.rows_dropped_null_symbol,
                    "rows_dropped_null_ts": prepared.rows_dropped_null_ts,
                },
            )

        try:
            self._table.append(prepared.arrow)
        except Exception as e:
            logger.exception(
                "bronze_iceberg_sink: append failed for %s: %s", file_date, e,
            )
            return SinkResult(
                sink=self.name, status="error",
                bars_written=0, error=str(e),
                metadata={"ingestion_run_id": ingestion_run_id},
            )

        snapshot_id: Optional[int] = None
        try:
            self._table.refresh()
            snap = self._table.current_snapshot()
            if snap is not None:
                snapshot_id = snap.snapshot_id
        except Exception:
            pass

        return SinkResult(
            sink=self.name,
            status="ok",
            bars_written=prepared.arrow.num_rows,
            metadata={
                "ingestion_run_id": ingestion_run_id,
                "ingestion_ts": ingestion_ts.isoformat(),
                "snapshot_id_after": snapshot_id,
                "rows_in": prepared.rows_in,
                "rows_dropped_null_symbol": prepared.rows_dropped_null_symbol,
                "rows_dropped_null_ts": prepared.rows_dropped_null_ts,
                "table": str(self._table.name()),
            },
        )


__all__ = ["BronzeIcebergSink"]
