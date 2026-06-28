"""Iceberg sink for canonical options chain snapshots."""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pyarrow as pa
from pyiceberg.table import Table

from app.services.iceberg_safe_upsert import chunked_upsert
from app.services.iceberg_catalog import get_catalog
from app.services.ingest.sinks import SinkResult
from app.services.options.parser import aggregate_gamma_exposure
from app.services.options.schemas import (
    GammaExposureSnapshot,
    OptionChainParseResult,
    OptionContractSnapshot,
    OptionExpirationSnapshot,
)
from app.services.options.tables import (
    ensure_chain_contracts,
    ensure_chain_raw,
    ensure_expirations,
    ensure_gamma_exposure,
)

log = logging.getLogger(__name__)


_RAW_ARROW = pa.schema([
    pa.field("underlying_symbol", pa.string(), nullable=False),
    pa.field("snapshot_ts", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("provider", pa.string(), nullable=False),
    pa.field("request_params", pa.string(), nullable=False),
    pa.field("status", pa.string(), nullable=False),
    pa.field("is_delayed", pa.bool_(), nullable=True),
    pa.field("underlying_price", pa.float64(), nullable=True),
    pa.field("raw_payload", pa.string(), nullable=False),
    pa.field("ingestion_ts", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("ingestion_run_id", pa.string(), nullable=False),
])

_CONTRACTS_ARROW = pa.schema([
    pa.field("underlying_symbol", pa.string(), nullable=False),
    pa.field("option_symbol", pa.string(), nullable=False),
    pa.field("snapshot_ts", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("put_call", pa.string(), nullable=False),
    pa.field("expiration_date", pa.date32(), nullable=False),
    pa.field("strike", pa.float64(), nullable=False),
    pa.field("underlying_price", pa.float64(), nullable=True),
    pa.field("days_to_expiration", pa.int32(), nullable=True),
    pa.field("bid", pa.float64(), nullable=True),
    pa.field("ask", pa.float64(), nullable=True),
    pa.field("last", pa.float64(), nullable=True),
    pa.field("mark", pa.float64(), nullable=True),
    pa.field("bid_size", pa.int64(), nullable=True),
    pa.field("ask_size", pa.int64(), nullable=True),
    pa.field("last_size", pa.int64(), nullable=True),
    pa.field("volume", pa.int64(), nullable=True),
    pa.field("open_interest", pa.int64(), nullable=True),
    pa.field("quote_time", pa.timestamp("us", tz="UTC"), nullable=True),
    pa.field("trade_time", pa.timestamp("us", tz="UTC"), nullable=True),
    pa.field("delta", pa.float64(), nullable=True),
    pa.field("gamma", pa.float64(), nullable=True),
    pa.field("theta", pa.float64(), nullable=True),
    pa.field("vega", pa.float64(), nullable=True),
    pa.field("rho", pa.float64(), nullable=True),
    pa.field("volatility", pa.float64(), nullable=True),
    pa.field("theoretical_value", pa.float64(), nullable=True),
    pa.field("intrinsic_value", pa.float64(), nullable=True),
    pa.field("time_value", pa.float64(), nullable=True),
    pa.field("in_the_money", pa.bool_(), nullable=True),
    pa.field("mini", pa.bool_(), nullable=True),
    pa.field("non_standard", pa.bool_(), nullable=True),
    pa.field("penny_pilot", pa.bool_(), nullable=True),
    pa.field("multiplier", pa.float64(), nullable=True),
    pa.field("settlement_type", pa.string(), nullable=True),
    pa.field("expiration_type", pa.string(), nullable=True),
    pa.field("source", pa.string(), nullable=False),
    pa.field("ingestion_ts", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("ingestion_run_id", pa.string(), nullable=False),
])

_EXPIRATIONS_ARROW = pa.schema([
    pa.field("underlying_symbol", pa.string(), nullable=False),
    pa.field("expiration_date", pa.date32(), nullable=False),
    pa.field("days_to_expiration", pa.int32(), nullable=True),
    pa.field("expiration_type", pa.string(), nullable=True),
    pa.field("settlement_type", pa.string(), nullable=True),
    pa.field("source", pa.string(), nullable=False),
    pa.field("observed_ts", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("ingestion_ts", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("ingestion_run_id", pa.string(), nullable=False),
])

_GAMMA_ARROW = pa.schema([
    pa.field("underlying_symbol", pa.string(), nullable=False),
    pa.field("snapshot_ts", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("expiration_date", pa.date32(), nullable=True),
    pa.field("strike", pa.float64(), nullable=True),
    pa.field("put_call", pa.string(), nullable=True),
    pa.field("underlying_price", pa.float64(), nullable=False),
    pa.field("gamma_exposure", pa.float64(), nullable=False),
    pa.field("call_gamma_exposure", pa.float64(), nullable=True),
    pa.field("put_gamma_exposure", pa.float64(), nullable=True),
    pa.field("net_gamma_exposure", pa.float64(), nullable=True),
    pa.field("open_interest", pa.int64(), nullable=True),
    pa.field("volume", pa.int64(), nullable=True),
    pa.field("contract_count", pa.int64(), nullable=True),
    pa.field("aggregation_level", pa.string(), nullable=False),
    pa.field("level_key", pa.string(), nullable=False),
    pa.field("methodology", pa.string(), nullable=False),
    pa.field("source", pa.string(), nullable=False),
    pa.field("source_snapshot_id", pa.string(), nullable=True),
    pa.field("ingestion_ts", pa.timestamp("us", tz="UTC"), nullable=False),
    pa.field("ingestion_run_id", pa.string(), nullable=False),
])


@dataclass(slots=True)
class _OptionsTables:
    raw: Table
    contracts: Table
    expirations: Table
    gamma: Table


class OptionsIcebergSink:
    """Write parsed option-chain snapshots to the options Iceberg domain."""

    name = "options_iceberg"

    def __init__(self, *, tables: _OptionsTables) -> None:
        self._tables = tables

    @classmethod
    def from_settings(cls) -> "OptionsIcebergSink":
        catalog = get_catalog()
        return cls(
            tables=_OptionsTables(
                raw=ensure_chain_raw(catalog),
                contracts=ensure_chain_contracts(catalog),
                expirations=ensure_expirations(catalog),
                gamma=ensure_gamma_exposure(catalog),
            )
        )

    async def write_parse_result(
        self,
        result: OptionChainParseResult | None,
        *,
        gamma_rows: list[GammaExposureSnapshot] | None = None,
    ) -> SinkResult:
        if result is None:
            return SinkResult(
                sink=self.name,
                status="skipped",
                bars_written=0,
                metadata={"reason": "empty_parse_result"},
            )

        ingestion_ts = _coerce_dt(result.raw_snapshot.ingestion_ts)
        ingestion_run_id = result.raw_snapshot.ingestion_run_id or str(uuid.uuid4())
        try:
            raw = pa.Table.from_pylist(
                [_raw_record(result, ingestion_ts, ingestion_run_id)],
                schema=_RAW_ARROW,
            )
            contracts = _table_from_contracts(result.contracts, ingestion_ts, ingestion_run_id)
            expirations = _table_from_expirations(result.expirations, ingestion_ts, ingestion_run_id)
            if gamma_rows is None:
                gamma_rows = aggregate_gamma_exposure(
                    result.contracts,
                    ingestion_run_id=ingestion_run_id,
                )
            gamma = _table_from_gamma(gamma_rows, ingestion_ts, ingestion_run_id)
        except Exception as e:
            log.exception("options_iceberg: prepare failed: %s", e)
            return SinkResult(sink=self.name, status="error", bars_written=0, error=str(e))

        appended: dict[str, int] = {}
        try:
            appended["raw"] = _upsert_if_rows(self._tables.raw, raw, "options.schwab_chain_raw")
            appended["contracts"] = _upsert_if_rows(
                self._tables.contracts, contracts, "options.schwab_chain_contracts"
            )
            appended["expirations"] = _upsert_if_rows(
                self._tables.expirations, expirations, "options.schwab_expirations"
            )
            appended["gamma"] = _upsert_if_rows(
                self._tables.gamma, gamma, "options.gamma_exposure_snapshots"
            )
        except Exception as e:
            log.exception("options_iceberg: append failed: %s", e)
            return SinkResult(
                sink=self.name,
                status="error",
                bars_written=0,
                error=str(e),
                metadata={"ingestion_run_id": ingestion_run_id, "rows_prepared": appended},
            )

        total_rows = sum(appended.values())
        if total_rows == 0:
            return SinkResult(
                sink=self.name,
                status="skipped",
                bars_written=0,
                metadata={"reason": "no_rows", "ingestion_run_id": ingestion_run_id},
            )

        return SinkResult(
            sink=self.name,
            status="ok",
            bars_written=total_rows,
            metadata={
                "ingestion_run_id": ingestion_run_id,
                "ingestion_ts": ingestion_ts.isoformat(),
                "rows": appended,
                "snapshots_after": {
                    "raw": _snapshot_id(self._tables.raw),
                    "contracts": _snapshot_id(self._tables.contracts),
                    "expirations": _snapshot_id(self._tables.expirations),
                    "gamma": _snapshot_id(self._tables.gamma),
                },
            },
        )


def _raw_record(
    result: OptionChainParseResult,
    ingestion_ts: datetime,
    ingestion_run_id: str,
) -> dict[str, Any]:
    raw = result.raw_snapshot
    return {
        "underlying_symbol": raw.underlying_symbol,
        "snapshot_ts": _coerce_dt(raw.snapshot_ts),
        "provider": raw.provider,
        "request_params": _json(raw.request_params),
        "status": raw.status,
        "is_delayed": raw.is_delayed,
        "underlying_price": raw.underlying_price,
        "raw_payload": _json(raw.raw_payload),
        "ingestion_ts": ingestion_ts,
        "ingestion_run_id": ingestion_run_id,
    }


def _table_from_contracts(
    contracts: list[OptionContractSnapshot],
    ingestion_ts: datetime,
    ingestion_run_id: str,
) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                **contract.model_dump(mode="python"),
                "snapshot_ts": _coerce_dt(contract.snapshot_ts),
                "quote_time": _coerce_dt(contract.quote_time) if contract.quote_time else None,
                "trade_time": _coerce_dt(contract.trade_time) if contract.trade_time else None,
                "ingestion_ts": _coerce_dt(contract.ingestion_ts) if contract.ingestion_ts else ingestion_ts,
                "ingestion_run_id": contract.ingestion_run_id or ingestion_run_id,
            }
            for contract in contracts
        ],
        schema=_CONTRACTS_ARROW,
    )


def _table_from_expirations(
    expirations: list[OptionExpirationSnapshot],
    ingestion_ts: datetime,
    ingestion_run_id: str,
) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                **expiration.model_dump(mode="python"),
                "observed_ts": _coerce_dt(expiration.observed_ts),
                "ingestion_ts": _coerce_dt(expiration.ingestion_ts) if expiration.ingestion_ts else ingestion_ts,
                "ingestion_run_id": expiration.ingestion_run_id or ingestion_run_id,
            }
            for expiration in expirations
        ],
        schema=_EXPIRATIONS_ARROW,
    )


def _table_from_gamma(
    rows: list[GammaExposureSnapshot],
    ingestion_ts: datetime,
    ingestion_run_id: str,
) -> pa.Table:
    return pa.Table.from_pylist(
        [
            {
                **row.model_dump(mode="python"),
                "snapshot_ts": _coerce_dt(row.snapshot_ts),
                "ingestion_ts": _coerce_dt(row.ingestion_ts) if row.ingestion_ts else ingestion_ts,
                "ingestion_run_id": row.ingestion_run_id or ingestion_run_id,
            }
            for row in rows
        ],
        schema=_GAMMA_ARROW,
    )


def _upsert_if_rows(table: Table, arrow: pa.Table, log_label: str) -> int:
    if arrow.num_rows == 0:
        return 0
    result = chunked_upsert(table, arrow, log_label=log_label)
    return result.total_rows


def _snapshot_id(table: Table) -> int | None:
    try:
        table.refresh()
        snap = table.current_snapshot()
        return snap.snapshot_id if snap is not None else None
    except Exception:
        return None


def _coerce_dt(value: datetime | None) -> datetime:
    if value is None:
        return datetime.now(timezone.utc).replace(microsecond=0)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


__all__ = ["OptionsIcebergSink"]
