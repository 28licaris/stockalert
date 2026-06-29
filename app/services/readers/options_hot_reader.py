"""ClickHouse reader for latest options hot-tier projections."""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any

from app.db.client import get_client
from app.services.options.hot_sink import CONTRACTS_TABLE, GEX_TABLE
from app.services.options.schemas import (
    GammaExposureSnapshot,
    LatestGammaExposureResponse,
    LatestOptionContractsResponse,
    OptionContractSnapshot,
)

logger = logging.getLogger(__name__)


class OptionsHotReader:
    """Read latest option contracts and GEX from ClickHouse."""

    def __init__(self, *, client: Any | None = None) -> None:
        self._client = client

    @classmethod
    def from_settings(cls) -> "OptionsHotReader":
        return cls()

    def get_latest_contracts(
        self,
        underlying_symbol: str,
        *,
        expiration_date: date | None = None,
        put_call: str | None = None,
        limit: int | None = None,
    ) -> LatestOptionContractsResponse:
        sym = (underlying_symbol or "").strip().upper()
        if not sym:
            return LatestOptionContractsResponse(
                underlying_symbol="",
                contracts=[],
                count=0,
            )

        clauses = ["underlying_symbol = {symbol:String}"]
        params: dict[str, Any] = {"symbol": sym, "limit": int(limit or 50_000)}
        if expiration_date is not None:
            clauses.append("expiration_date = {expiration_date:Date}")
            params["expiration_date"] = expiration_date
        if put_call:
            clauses.append("put_call = {put_call:String}")
            params["put_call"] = put_call.strip().upper()

        query = f"""
            SELECT
                underlying_symbol, option_symbol, snapshot_ts, put_call,
                expiration_date, strike, underlying_price, days_to_expiration,
                bid, ask, last, mark, volume, open_interest, delta, gamma,
                theta, vega, rho, volatility, in_the_money, multiplier,
                source, ingestion_ts, ingestion_run_id
            FROM {CONTRACTS_TABLE} FINAL
            WHERE {" AND ".join(clauses)}
            ORDER BY expiration_date ASC, strike ASC, put_call ASC, option_symbol ASC
            LIMIT {{limit:UInt32}}
        """
        try:
            rows = self._client_or_default().query(
                query,
                parameters=params,
            ).result_rows
        except Exception as e:
            logger.warning(
                "OptionsHotReader: contracts query failed for %s: %s",
                sym,
                e,
            )
            return LatestOptionContractsResponse(
                underlying_symbol=sym,
                contracts=[],
                count=0,
            )

        contracts = [_contract_from_row(row) for row in rows]
        return LatestOptionContractsResponse(
            underlying_symbol=sym,
            contracts=contracts,
            count=len(contracts),
        )

    def get_latest_gamma_exposure(
        self,
        underlying_symbol: str,
        *,
        aggregation_level: str | None = None,
        limit: int | None = None,
    ) -> LatestGammaExposureResponse:
        sym = (underlying_symbol or "").strip().upper()
        if not sym:
            return LatestGammaExposureResponse(
                underlying_symbol="",
                aggregation_level=aggregation_level,
                rows=[],
                count=0,
            )

        clauses = ["underlying_symbol = {symbol:String}"]
        params: dict[str, Any] = {"symbol": sym, "limit": int(limit or 50_000)}
        if aggregation_level:
            clauses.append("aggregation_level = {aggregation_level:String}")
            params["aggregation_level"] = aggregation_level

        query = f"""
            SELECT
                underlying_symbol, snapshot_ts, aggregation_level, level_key,
                expiration_date, strike, put_call, underlying_price,
                gamma_exposure, call_gamma_exposure, put_gamma_exposure,
                net_gamma_exposure, open_interest, volume, contract_count,
                methodology, source, source_snapshot_id, ingestion_ts,
                ingestion_run_id
            FROM {GEX_TABLE} FINAL
            WHERE {" AND ".join(clauses)}
            ORDER BY aggregation_level ASC, expiration_date ASC, strike ASC, level_key ASC
            LIMIT {{limit:UInt32}}
        """
        try:
            rows = self._client_or_default().query(
                query,
                parameters=params,
            ).result_rows
        except Exception as e:
            logger.warning("OptionsHotReader: GEX query failed for %s: %s", sym, e)
            return LatestGammaExposureResponse(
                underlying_symbol=sym,
                aggregation_level=aggregation_level,
                rows=[],
                count=0,
            )

        gex_rows = [_gex_from_row(row) for row in rows]
        return LatestGammaExposureResponse(
            underlying_symbol=sym,
            aggregation_level=aggregation_level,
            rows=gex_rows,
            count=len(gex_rows),
        )

    def _client_or_default(self):
        return self._client or get_client()


def _contract_from_row(row) -> OptionContractSnapshot:
    return OptionContractSnapshot(
        underlying_symbol=row[0],
        option_symbol=row[1],
        snapshot_ts=_utc(row[2]),
        put_call=row[3],
        expiration_date=row[4],
        strike=float(row[5]),
        underlying_price=_float_or_none(row[6]),
        days_to_expiration=row[7],
        bid=_float_or_none(row[8]),
        ask=_float_or_none(row[9]),
        last=_float_or_none(row[10]),
        mark=_float_or_none(row[11]),
        volume=row[12],
        open_interest=row[13],
        delta=_float_or_none(row[14]),
        gamma=_float_or_none(row[15]),
        theta=_float_or_none(row[16]),
        vega=_float_or_none(row[17]),
        rho=_float_or_none(row[18]),
        volatility=_float_or_none(row[19]),
        in_the_money=None if row[20] is None else bool(row[20]),
        multiplier=_float_or_none(row[21]),
        source=row[22] or "schwab-chain",
        ingestion_ts=_utc(row[23]),
        ingestion_run_id=row[24],
    )


def _gex_from_row(row) -> GammaExposureSnapshot:
    return GammaExposureSnapshot(
        underlying_symbol=row[0],
        snapshot_ts=_utc(row[1]),
        aggregation_level=row[2],
        level_key=row[3],
        expiration_date=row[4],
        strike=_float_or_none(row[5]),
        put_call=row[6],
        underlying_price=float(row[7]),
        gamma_exposure=float(row[8]),
        call_gamma_exposure=_float_or_none(row[9]),
        put_gamma_exposure=_float_or_none(row[10]),
        net_gamma_exposure=_float_or_none(row[11]),
        open_interest=row[12],
        volume=row[13],
        contract_count=row[14],
        methodology=row[15],
        source=row[16],
        source_snapshot_id=row[17],
        ingestion_ts=_utc(row[18]),
        ingestion_run_id=row[19],
    )


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _float_or_none(value) -> float | None:
    return None if value is None else float(value)
