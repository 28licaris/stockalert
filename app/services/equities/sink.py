"""
EquitiesIcebergSink — canonical writer for the architecture-v2
`equities.*` Iceberg tables.

Inherits the v1 bronze sink design (append-only, ingestion-stamped,
upstream idempotency) because it's the right model for v2's cadence
too: PyIceberg's `merge_rows` / `overwrite(filter)` reads existing
files at write time — hundreds of MB of I/O per call, unacceptable
for nightly whole-market writes and sub-second live writes. Idempotency
lives UPSTREAM in `ingestion_runs` watermarks (set by
`nightly_polygon_refresh`, the live Schwab writer, and the history
backfill script). The v1 `app.services.bronze.sink.BronzeIcebergSink`
module was deleted in CV14; this is the canonical Iceberg writer now.

Tables targeted by this sink:

  - `equities.polygon_raw`      via `for_polygon_raw()`
  - `equities.schwab_universe`  via `for_schwab_universe()`

`equities.polygon_adjusted` is populated by the Spark adjustment job
(CV5), not by this sink. `equities.market_corp_actions` has its own
writer in `app/services/ingest/corp_actions.py` (CV9) — different row
shape, different cadence, separate code path.

Schema notes:

  - `polygon_raw` is 12 canonical OHLCV cols (same shape the v1 bronze
    polygon table had — the schema was preserved 1:1 across the
    migration so the Athena bulk-import in `scripts/lake_import_athena.py`
    is a straight copy).
  - `schwab_universe` is the same 12 cols PLUS `adj_factor` (required,
    Gate 2). Schwab returns pre-adjusted prices, so the sink stamps
    `adj_factor = 1.0` on every row. The column exists for schema
    parity with `polygon_adjusted` so cross-provider UNION queries
    (the ML use case in `docs/architecture_v2/02_schema.md`) don't
    need column massaging.

Data-quality boundary (same as v1): rows missing `symbol` or
`timestamp` are dropped before write.
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

from app.services.equities.tables import (
    ensure_polygon_raw,
    ensure_schwab_universe,
)
from app.services.iceberg_catalog import get_catalog
from app.services.ingest.sinks import Kind, SinkResult

logger = logging.getLogger(__name__)


# 12 canonical OHLCV columns — `equities.polygon_raw` shape. Matches
# the v1 _BRONZE_MINUTE_ARROW exactly so the CV3 history-backfill can
# stream rows out of the existing Polygon flat-files reader without
# any column transformation (per docs/architecture_v2/02_schema.md).
_POLYGON_RAW_ARROW = pa.schema(
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

# Same as _POLYGON_RAW_ARROW + `adj_factor` (required, default 1.0).
# Schwab's REST + WebSocket both return pre-adjusted prices; we can't
# back out a real adj_factor from Schwab data, so 1.0 is the literal
# truth at write time (Gate 2 decision in
# docs/architecture_v2/08_decisions.md).
_SCHWAB_UNIVERSE_ARROW = pa.schema(
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
        pa.field("adj_factor", pa.float64(), nullable=False),
    ]
)


@dataclass(slots=True)
class _PreparedFrame:
    arrow: pa.Table
    rows_in: int
    rows_dropped_null_symbol: int
    rows_dropped_null_ts: int


def _prepare_ohlcv_frame(
    df: pd.DataFrame,
    *,
    target_schema: pa.Schema,
    ingestion_run_id: str,
    ingestion_ts: datetime,
    adj_factor: Optional[float] = None,
) -> _PreparedFrame:
    """
    Project the canonical-shape DataFrame down to the target Iceberg
    schema and cast/clean types so PyIceberg can write without complaint.

    - Drops rows with NULL symbol or timestamp (data-quality boundary).
    - Treats `vwap == 0.0` as NULL (Polygon flat-files emit 0.0 as a
      placeholder; future writers emit NULL directly).
    - Stamps `ingestion_ts` + `ingestion_run_id` on every row.
    - If `adj_factor` is provided (Schwab path), stamps it on every row.
      `polygon_raw` callers pass `adj_factor=None` and the column is
      not added.
    """
    rows_in = len(df)

    base_cols = [
        "symbol", "timestamp", "open", "high", "low", "close",
        "volume", "vwap", "trade_count", "source",
    ]
    missing = [c for c in base_cols if c not in df.columns]
    if missing:
        raise ValueError(
            f"EquitiesIcebergSink: input frame missing required columns: {missing}"
        )

    work = df.loc[:, base_cols].copy()

    null_sym = work["symbol"].isna() | (work["symbol"].astype(str).str.len() == 0)
    null_ts = work["timestamp"].isna()
    rows_dropped_null_symbol = int(null_sym.sum())
    rows_dropped_null_ts = int(null_ts.sum())
    work = work.loc[~(null_sym | null_ts)]

    if "vwap" in work.columns:
        work.loc[work["vwap"] == 0.0, "vwap"] = pd.NA

    work["ingestion_ts"] = ingestion_ts
    work["ingestion_run_id"] = ingestion_run_id

    if adj_factor is not None:
        work["adj_factor"] = float(adj_factor)

    arrow = pa.Table.from_pandas(work, schema=target_schema, preserve_index=False)

    return _PreparedFrame(
        arrow=arrow,
        rows_in=rows_in,
        rows_dropped_null_symbol=rows_dropped_null_symbol,
        rows_dropped_null_ts=rows_dropped_null_ts,
    )


class EquitiesIcebergSink:
    """
    Iceberg sink for `equities.*` tables that share the canonical
    OHLCV minute-bar shape (`polygon_raw`, `schwab_universe`).

    Construct via:
      - `EquitiesIcebergSink.for_polygon_raw()`
      - `EquitiesIcebergSink.for_schwab_universe()`

    Or directly for one-off / test use:
      `EquitiesIcebergSink(
           table=...,
           name=...,
           arrow_schema=...,
           accepted_providers={...},
           static_adj_factor=...,
       )`
    """

    def __init__(
        self,
        *,
        table: Table,
        name: str,
        arrow_schema: pa.Schema,
        accepted_providers: set[tuple[str, str]] | None = None,
        static_adj_factor: Optional[float] = None,
    ) -> None:
        if table is None:
            raise ValueError("EquitiesIcebergSink: table is required")
        self._table = table
        self._name = name
        self._arrow_schema = arrow_schema
        # None = accept any provider/kind (useful for test fixtures).
        self._accepted_providers = accepted_providers
        self._static_adj_factor = static_adj_factor

    @property
    def name(self) -> str:
        return self._name

    @property
    def table(self) -> Table:
        return self._table

    @classmethod
    def for_polygon_raw(cls) -> "EquitiesIcebergSink":
        catalog = get_catalog()
        table = ensure_polygon_raw(catalog)
        return cls(
            table=table,
            name="equities_polygon_raw",
            arrow_schema=_POLYGON_RAW_ARROW,
            accepted_providers={
                ("polygon", "minute"),
                ("polygon-flatfiles", "minute"),
            },
        )

    @classmethod
    def for_schwab_universe(cls) -> "EquitiesIcebergSink":
        catalog = get_catalog()
        table = ensure_schwab_universe(catalog)
        return cls(
            table=table,
            name="equities_schwab_universe",
            arrow_schema=_SCHWAB_UNIVERSE_ARROW,
            accepted_providers={
                ("schwab", "minute"),
                ("schwab-rest", "minute"),
                ("schwab-live", "minute"),
            },
            static_adj_factor=1.0,
        )

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
            prepared = _prepare_ohlcv_frame(
                df,
                target_schema=self._arrow_schema,
                ingestion_run_id=ingestion_run_id,
                ingestion_ts=ingestion_ts,
                adj_factor=self._static_adj_factor,
            )
        except Exception as e:
            logger.exception(
                "equities_iceberg_sink[%s]: prepare failed for %s: %s",
                self._name, file_date, e,
            )
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
                "equities_iceberg_sink[%s]: append failed for %s: %s",
                self._name, file_date, e,
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


__all__ = ["EquitiesIcebergSink"]
