"""ClickHouse hot-tier sink for latest options snapshots."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from app.db.client import get_client
from app.services.ingest.sinks import SinkResult
from app.services.options.parser import aggregate_gamma_exposure
from app.services.options.schemas import (
    GammaExposureSnapshot,
    OptionChainParseResult,
    OptionContractSnapshot,
)

log = logging.getLogger(__name__)

CONTRACTS_TABLE = "options_contracts_latest"
GEX_TABLE = "options_gex_latest"

CONTRACT_COLUMNS = [
    "underlying_symbol",
    "option_symbol",
    "snapshot_ts",
    "put_call",
    "expiration_date",
    "strike",
    "underlying_price",
    "days_to_expiration",
    "bid",
    "ask",
    "last",
    "mark",
    "volume",
    "open_interest",
    "delta",
    "gamma",
    "theta",
    "vega",
    "rho",
    "volatility",
    "in_the_money",
    "multiplier",
    "source",
    "ingestion_ts",
    "ingestion_run_id",
    "version",
]

GEX_COLUMNS = [
    "underlying_symbol",
    "snapshot_ts",
    "aggregation_level",
    "level_key",
    "expiration_date",
    "strike",
    "put_call",
    "underlying_price",
    "gamma_exposure",
    "call_gamma_exposure",
    "put_gamma_exposure",
    "net_gamma_exposure",
    "open_interest",
    "volume",
    "contract_count",
    "methodology",
    "source",
    "source_snapshot_id",
    "ingestion_ts",
    "ingestion_run_id",
    "version",
]


class OptionsClickHouseSink:
    """Write latest option contracts and GEX rows to ClickHouse."""

    name = "options_clickhouse_hot"

    def __init__(self, *, client: Any | None = None) -> None:
        self._client = client

    @classmethod
    def from_settings(cls) -> "OptionsClickHouseSink":
        return cls()

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
        ingestion_run_id = result.raw_snapshot.ingestion_run_id or ""
        version = _version(result.raw_snapshot.snapshot_ts)
        gamma_rows = gamma_rows if gamma_rows is not None else aggregate_gamma_exposure(
            result.contracts,
            ingestion_run_id=ingestion_run_id,
        )

        contract_rows = [
            _contract_row(contract, ingestion_ts, ingestion_run_id, version)
            for contract in result.contracts
        ]
        gex_rows = [
            _gex_row(row, ingestion_ts, ingestion_run_id, version)
            for row in gamma_rows
        ]

        if not contract_rows and not gex_rows:
            return SinkResult(
                sink=self.name,
                status="skipped",
                bars_written=0,
                metadata={"reason": "no_hot_rows"},
            )

        try:
            client = self._client or get_client()
            if contract_rows:
                client.insert(
                    CONTRACTS_TABLE,
                    contract_rows,
                    column_names=CONTRACT_COLUMNS,
                )
            if gex_rows:
                client.insert(GEX_TABLE, gex_rows, column_names=GEX_COLUMNS)
        except Exception as e:
            log.exception("options_clickhouse_hot: insert failed: %s", e)
            return SinkResult(
                sink=self.name,
                status="error",
                bars_written=0,
                error=str(e),
                metadata={
                    "rows_prepared": {
                        "contracts": len(contract_rows),
                        "gex": len(gex_rows),
                    }
                },
            )

        rows_written = len(contract_rows) + len(gex_rows)
        return SinkResult(
            sink=self.name,
            status="ok",
            bars_written=rows_written,
            metadata={
                "rows": {"contracts": len(contract_rows), "gex": len(gex_rows)},
                "ingestion_run_id": ingestion_run_id,
            },
        )


def _contract_row(
    contract: OptionContractSnapshot,
    ingestion_ts: datetime,
    ingestion_run_id: str,
    version: int,
) -> list[Any]:
    return [
        contract.underlying_symbol,
        contract.option_symbol,
        _coerce_dt(contract.snapshot_ts),
        contract.put_call,
        contract.expiration_date,
        float(contract.strike),
        _float_or_none(contract.underlying_price),
        contract.days_to_expiration,
        _float_or_none(contract.bid),
        _float_or_none(contract.ask),
        _float_or_none(contract.last),
        _float_or_none(contract.mark),
        _int_or_none(contract.volume),
        _int_or_none(contract.open_interest),
        _float_or_none(contract.delta),
        _float_or_none(contract.gamma),
        _float_or_none(contract.theta),
        _float_or_none(contract.vega),
        _float_or_none(contract.rho),
        _float_or_none(contract.volatility),
        _bool_or_none(contract.in_the_money),
        _float_or_none(contract.multiplier),
        contract.source,
        ingestion_ts,
        ingestion_run_id,
        version,
    ]


def _gex_row(
    row: GammaExposureSnapshot,
    ingestion_ts: datetime,
    ingestion_run_id: str,
    version: int,
) -> list[Any]:
    return [
        row.underlying_symbol,
        _coerce_dt(row.snapshot_ts),
        row.aggregation_level,
        row.level_key,
        row.expiration_date,
        _float_or_none(row.strike),
        row.put_call,
        float(row.underlying_price),
        float(row.gamma_exposure),
        _float_or_none(row.call_gamma_exposure),
        _float_or_none(row.put_gamma_exposure),
        _float_or_none(row.net_gamma_exposure),
        _int_or_none(row.open_interest),
        _int_or_none(row.volume),
        _int_or_none(row.contract_count),
        row.methodology,
        row.source,
        row.source_snapshot_id,
        ingestion_ts,
        ingestion_run_id,
        version,
    ]


def _coerce_dt(value: datetime | None) -> datetime:
    dt = value or datetime.now(timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _version(value: datetime | None) -> int:
    return int(_coerce_dt(value).timestamp() * 1000)


def _float_or_none(value: float | int | None) -> float | None:
    return None if value is None else float(value)


def _int_or_none(value: int | None) -> int | None:
    return None if value is None else int(value)


def _bool_or_none(value: bool | None) -> int | None:
    return None if value is None else int(bool(value))
