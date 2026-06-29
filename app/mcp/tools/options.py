"""MCP tools for options contracts and gamma exposure."""
from __future__ import annotations

from datetime import date, datetime
from functools import lru_cache
from typing import Optional

from app.mcp.middleware import tool_call
from app.mcp.server import mcp
from app.services.options.schemas import (
    GammaExposureResponse,
    LatestGammaExposureResponse,
    LatestOptionContractsResponse,
    OptionContractsResponse,
)
from app.services.readers.options_hot_reader import OptionsHotReader
from app.services.readers.options_reader import OptionsReader


@lru_cache(maxsize=1)
def _reader() -> OptionsReader:
    return OptionsReader.from_settings()


@lru_cache(maxsize=1)
def _hot_reader() -> OptionsHotReader:
    return OptionsHotReader.from_settings()


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    return date.fromisoformat(value)


@mcp.tool()
def get_option_contracts(
    symbol: str,
    start: datetime,
    end: datetime,
    expiration_date: Optional[str] = None,
    put_call: Optional[str] = None,
    snapshot_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> OptionContractsResponse:
    """Return canonical option contract snapshots from the options lake.

    USE WHEN: an agent needs the option chain state that was captured
    from Schwab for backtests, simulated trading, alert decisions, or
    market-opportunity scans.

    Args:
        symbol: Underlying symbol, e.g. AAPL.
        start: Snapshot window start, inclusive. Naive datetimes treated as UTC.
        end: Snapshot window end, exclusive.
        expiration_date: Optional expiration filter as YYYY-MM-DD.
        put_call: Optional side filter, CALL or PUT.
        snapshot_id: Optional Iceberg snapshot id for deterministic replay.
        limit: Return at most the most recent N contracts in the window.
    """
    with tool_call("get_option_contracts", symbol=symbol):
        return _reader().get_contracts(
            symbol,
            start,
            end,
            expiration_date=_parse_date(expiration_date),
            put_call=put_call,
            snapshot_id=snapshot_id,
            limit=limit,
        )


@mcp.tool()
def get_latest_option_contracts(
    symbol: str,
    expiration_date: Optional[str] = None,
    put_call: Optional[str] = None,
    limit: Optional[int] = None,
) -> LatestOptionContractsResponse:
    """Return latest option contracts from the ClickHouse hot tier.

    USE WHEN: an agent needs current chain context for alerts, UI
    decisions, or opportunity scans and does not need historical replay.
    The S3 lake remains the canonical source for backtests.
    """
    with tool_call("get_latest_option_contracts", symbol=symbol):
        return _hot_reader().get_latest_contracts(
            symbol,
            expiration_date=_parse_date(expiration_date),
            put_call=put_call,
            limit=limit,
        )


@mcp.tool()
def get_latest_option_gamma_exposure(
    symbol: str,
    aggregation_level: Optional[str] = None,
    limit: Optional[int] = None,
) -> LatestGammaExposureResponse:
    """Return latest GEX rows from the ClickHouse hot tier.

    USE WHEN: an agent needs current gamma context for support,
    resistance, volatility-regime, or alert decisions with low latency.
    """
    with tool_call("get_latest_option_gamma_exposure", symbol=symbol):
        return _hot_reader().get_latest_gamma_exposure(
            symbol,
            aggregation_level=aggregation_level,
            limit=limit,
        )


@mcp.tool()
def get_option_gamma_exposure(
    symbol: str,
    start: datetime,
    end: datetime,
    aggregation_level: Optional[str] = None,
    snapshot_id: Optional[str] = None,
    limit: Optional[int] = None,
) -> GammaExposureResponse:
    """Return derived gamma exposure rows from the options lake.

    USE WHEN: an agent needs GEX context for support/resistance zones,
    volatility-regime reasoning, alert generation, or simulated option
    strategy evaluation.

    Args:
        symbol: Underlying symbol, e.g. AAPL.
        start: Snapshot window start, inclusive. Naive datetimes treated as UTC.
        end: Snapshot window end, exclusive.
        aggregation_level: Optional level: total, strike, expiry, or strike_expiry.
        snapshot_id: Optional Iceberg snapshot id for deterministic replay.
        limit: Return at most the most recent N GEX rows in the window.
    """
    with tool_call("get_option_gamma_exposure", symbol=symbol):
        return _reader().get_gamma_exposure(
            symbol,
            start,
            end,
            aggregation_level=aggregation_level,
            snapshot_id=snapshot_id,
            limit=limit,
        )
