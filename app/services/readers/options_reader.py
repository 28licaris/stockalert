"""OptionsReader — read service for canonical options lake tables."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Optional

import pyarrow.compute as pc
from pyiceberg.expressions import And, EqualTo, GreaterThanOrEqual, LessThan
from pyiceberg.table import Table

from app.services.iceberg_catalog import get_catalog
from app.services.options.schemas import (
    GammaExposureResponse,
    GammaExposureSnapshot,
    OptionContractsResponse,
    OptionContractSnapshot,
)
from app.services.options.tables import (
    CHAIN_CONTRACTS_TABLE_NAME,
    GAMMA_EXPOSURE_TABLE_NAME,
    options_table_id,
)

logger = logging.getLogger(__name__)

_CONTRACT_FIELDS = (
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
    "bid_size",
    "ask_size",
    "last_size",
    "volume",
    "open_interest",
    "quote_time",
    "trade_time",
    "delta",
    "gamma",
    "theta",
    "vega",
    "rho",
    "volatility",
    "theoretical_value",
    "intrinsic_value",
    "time_value",
    "in_the_money",
    "mini",
    "non_standard",
    "penny_pilot",
    "multiplier",
    "settlement_type",
    "expiration_type",
    "source",
    "ingestion_ts",
    "ingestion_run_id",
)

_GAMMA_FIELDS = (
    "underlying_symbol",
    "snapshot_ts",
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
    "aggregation_level",
    "level_key",
    "methodology",
    "source",
    "source_snapshot_id",
    "ingestion_ts",
    "ingestion_run_id",
)


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _snapshot_id(table: Table) -> str | None:
    snap = table.current_snapshot()
    return str(snap.snapshot_id) if snap else None


def _scan_snapshot_id(value: str | int | None) -> int | None:
    if value is None:
        return None
    return int(value)


class OptionsReader:
    """Read Schwab option-chain contract and GEX snapshots from Iceberg."""

    def __init__(
        self,
        *,
        catalog=None,
        contracts_table: Optional[Table] = None,
        gamma_table: Optional[Table] = None,
    ) -> None:
        self._catalog = catalog
        self._contracts_table = contracts_table
        self._gamma_table = gamma_table

    @classmethod
    def from_settings(cls) -> "OptionsReader":
        return cls()

    def _get_contracts_table(self) -> Table:
        if self._contracts_table is None:
            catalog = self._catalog or get_catalog()
            self._contracts_table = catalog.load_table(
                options_table_id(CHAIN_CONTRACTS_TABLE_NAME)
            )
        return self._contracts_table

    def _get_gamma_table(self) -> Table:
        if self._gamma_table is None:
            catalog = self._catalog or get_catalog()
            self._gamma_table = catalog.load_table(
                options_table_id(GAMMA_EXPOSURE_TABLE_NAME)
            )
        return self._gamma_table

    def get_contracts(
        self,
        underlying_symbol: str,
        start: datetime,
        end: datetime,
        *,
        expiration_date: date | None = None,
        put_call: str | None = None,
        snapshot_id: str | int | None = None,
        limit: int | None = None,
    ) -> OptionContractsResponse:
        sym = (underlying_symbol or "").strip().upper()
        start_utc = _utc(start)
        end_utc = _utc(end)
        if not sym or end_utc <= start_utc:
            return OptionContractsResponse(
                underlying_symbol=sym,
                start=start_utc,
                end=end_utc,
                snapshot_id=None,
                contracts=[],
                count=0,
            )

        try:
            table = self._get_contracts_table()
        except Exception as e:
            logger.warning(
                "OptionsReader: %s not loadable (%s); returning empty contracts",
                options_table_id(CHAIN_CONTRACTS_TABLE_NAME),
                e,
            )
            return OptionContractsResponse(
                underlying_symbol=sym,
                start=start_utc,
                end=end_utc,
                snapshot_id=None,
                contracts=[],
                count=0,
            )

        clauses = [
            EqualTo("underlying_symbol", sym),
            GreaterThanOrEqual("snapshot_ts", start_utc.isoformat()),
            LessThan("snapshot_ts", end_utc.isoformat()),
        ]
        if expiration_date is not None:
            clauses.append(EqualTo("expiration_date", expiration_date))
        if put_call:
            clauses.append(EqualTo("put_call", put_call.strip().upper()))

        try:
            scan_kwargs = {
                "row_filter": And(*clauses),
                "selected_fields": _CONTRACT_FIELDS,
            }
            pinned_snapshot = _scan_snapshot_id(snapshot_id)
            if pinned_snapshot is not None:
                scan_kwargs["snapshot_id"] = pinned_snapshot
            arrow = table.scan(**scan_kwargs).to_arrow()
        except Exception as e:
            logger.warning(
                "OptionsReader: contract scan failed for %s: %s; returning empty",
                sym,
                e,
            )
            return OptionContractsResponse(
                underlying_symbol=sym,
                start=start_utc,
                end=end_utc,
                snapshot_id=None,
                contracts=[],
                count=0,
            )

        rows = self._contracts_from_arrow(arrow, limit=limit)
        return OptionContractsResponse(
            underlying_symbol=sym,
            start=start_utc,
            end=end_utc,
            snapshot_id=(
                str(snapshot_id) if snapshot_id is not None else _snapshot_id(table)
            ),
            contracts=rows,
            count=len(rows),
        )

    def get_gamma_exposure(
        self,
        underlying_symbol: str,
        start: datetime,
        end: datetime,
        *,
        aggregation_level: str | None = None,
        snapshot_id: str | int | None = None,
        limit: int | None = None,
    ) -> GammaExposureResponse:
        sym = (underlying_symbol or "").strip().upper()
        start_utc = _utc(start)
        end_utc = _utc(end)
        if not sym or end_utc <= start_utc:
            return GammaExposureResponse(
                underlying_symbol=sym,
                start=start_utc,
                end=end_utc,
                aggregation_level=None,
                snapshot_id=None,
                rows=[],
                count=0,
            )

        try:
            table = self._get_gamma_table()
        except Exception as e:
            logger.warning(
                "OptionsReader: %s not loadable (%s); returning empty GEX",
                options_table_id(GAMMA_EXPOSURE_TABLE_NAME),
                e,
            )
            return GammaExposureResponse(
                underlying_symbol=sym,
                start=start_utc,
                end=end_utc,
                aggregation_level=aggregation_level,
                snapshot_id=None,
                rows=[],
                count=0,
            )

        clauses = [
            EqualTo("underlying_symbol", sym),
            GreaterThanOrEqual("snapshot_ts", start_utc.isoformat()),
            LessThan("snapshot_ts", end_utc.isoformat()),
        ]
        if aggregation_level:
            clauses.append(EqualTo("aggregation_level", aggregation_level))

        try:
            scan_kwargs = {
                "row_filter": And(*clauses),
                "selected_fields": _GAMMA_FIELDS,
            }
            pinned_snapshot = _scan_snapshot_id(snapshot_id)
            if pinned_snapshot is not None:
                scan_kwargs["snapshot_id"] = pinned_snapshot
            arrow = table.scan(**scan_kwargs).to_arrow()
        except Exception as e:
            logger.warning(
                "OptionsReader: GEX scan failed for %s: %s; returning empty",
                sym,
                e,
            )
            return GammaExposureResponse(
                underlying_symbol=sym,
                start=start_utc,
                end=end_utc,
                aggregation_level=aggregation_level,
                snapshot_id=None,
                rows=[],
                count=0,
            )

        rows = self._gamma_from_arrow(arrow, limit=limit)
        return GammaExposureResponse(
            underlying_symbol=sym,
            start=start_utc,
            end=end_utc,
            aggregation_level=aggregation_level,
            snapshot_id=(
                str(snapshot_id) if snapshot_id is not None else _snapshot_id(table)
            ),
            rows=rows,
            count=len(rows),
        )

    @staticmethod
    def _contracts_from_arrow(
        arrow,
        *,
        limit: int | None = None,
    ) -> list[OptionContractSnapshot]:
        if arrow.num_rows == 0:
            return []
        arrow = arrow.take(
            pc.sort_indices(
                arrow,
                sort_keys=[
                    ("snapshot_ts", "ascending"),
                    ("expiration_date", "ascending"),
                    ("strike", "ascending"),
                    ("put_call", "ascending"),
                    ("option_symbol", "ascending"),
                ],
            )
        )
        if limit is not None and arrow.num_rows > limit:
            arrow = arrow.slice(arrow.num_rows - limit, limit)
        return [OptionContractSnapshot(**row) for row in arrow.to_pylist()]

    @staticmethod
    def _gamma_from_arrow(
        arrow,
        *,
        limit: int | None = None,
    ) -> list[GammaExposureSnapshot]:
        if arrow.num_rows == 0:
            return []
        rows = arrow.to_pylist()
        rows.sort(
            key=lambda row: (
                row["snapshot_ts"],
                row["aggregation_level"],
                row["expiration_date"] or date.min,
                row["strike"] if row["strike"] is not None else float("-inf"),
                row["level_key"],
            )
        )
        if limit is not None and len(rows) > limit:
            rows = rows[-limit:]
        return [GammaExposureSnapshot(**row) for row in rows]
