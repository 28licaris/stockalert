"""Pydantic contracts for options data.

These DTOs are the public boundary for options ingestion, readers, API routes,
MCP tools, alerts, and simulations. Provider-native payloads stay in raw
snapshot contracts; canonical contract rows do not expose provider response
shapes.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


PutCall = Literal["CALL", "PUT"]
GammaAggregationLevel = Literal["total", "strike", "expiry", "strike_expiry"]
IngestStatus = Literal["ok", "skipped", "error"]


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _upper(value: str) -> str:
    return value.strip().upper()


class OptionChainRawSnapshot(BaseModel):
    """One raw provider option-chain response kept for audit/reparse."""

    underlying_symbol: str
    snapshot_ts: datetime
    provider: str = "schwab"
    request_params: dict[str, Any] = Field(default_factory=dict)
    status: str = ""
    is_delayed: bool | None = None
    underlying_price: float | None = None
    raw_payload: dict[str, Any]
    ingestion_ts: datetime | None = None
    ingestion_run_id: str | None = None

    @field_validator("underlying_symbol", mode="before")
    @classmethod
    def _normalize_upper(cls, value: str) -> str:
        return _upper(str(value))

    @field_validator("snapshot_ts", "ingestion_ts", mode="after")
    @classmethod
    def _normalize_ts(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None


class OptionContractSnapshot(BaseModel):
    """One canonical option contract at one chain snapshot timestamp."""

    underlying_symbol: str
    option_symbol: str
    snapshot_ts: datetime
    put_call: PutCall
    expiration_date: date
    strike: float
    underlying_price: float | None = None
    days_to_expiration: int | None = None
    bid: float | None = None
    ask: float | None = None
    last: float | None = None
    mark: float | None = None
    bid_size: int | None = None
    ask_size: int | None = None
    last_size: int | None = None
    volume: int | None = None
    open_interest: int | None = None
    quote_time: datetime | None = None
    trade_time: datetime | None = None
    delta: float | None = None
    gamma: float | None = None
    theta: float | None = None
    vega: float | None = None
    rho: float | None = None
    volatility: float | None = None
    theoretical_value: float | None = None
    intrinsic_value: float | None = None
    time_value: float | None = None
    in_the_money: bool | None = None
    mini: bool | None = None
    non_standard: bool | None = None
    penny_pilot: bool | None = None
    multiplier: float | None = None
    settlement_type: str | None = None
    expiration_type: str | None = None
    source: str = "schwab-chain"
    ingestion_ts: datetime | None = None
    ingestion_run_id: str | None = None

    @field_validator("underlying_symbol", "put_call", mode="before")
    @classmethod
    def _normalize_upper(cls, value: str) -> str:
        return _upper(str(value))

    @field_validator(
        "snapshot_ts",
        "quote_time",
        "trade_time",
        "ingestion_ts",
        mode="after",
    )
    @classmethod
    def _normalize_ts(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None

    @model_validator(mode="after")
    def _validate_prices(self) -> "OptionContractSnapshot":
        if self.strike <= 0:
            raise ValueError("strike must be positive")
        return self


class OptionExpirationSnapshot(BaseModel):
    """One observed expiration for an optionable underlying."""

    underlying_symbol: str
    expiration_date: date
    days_to_expiration: int | None = None
    expiration_type: str | None = None
    settlement_type: str | None = None
    source: str = "schwab-expirationchain"
    observed_ts: datetime
    ingestion_ts: datetime | None = None
    ingestion_run_id: str | None = None

    @field_validator("underlying_symbol", mode="before")
    @classmethod
    def _normalize_upper(cls, value: str) -> str:
        return _upper(str(value))

    @field_validator("observed_ts", "ingestion_ts", mode="after")
    @classmethod
    def _normalize_ts(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None


class OptionChainParseResult(BaseModel):
    """Canonical result of parsing one provider chain response."""

    raw_snapshot: OptionChainRawSnapshot
    contracts: list[OptionContractSnapshot] = Field(default_factory=list)
    expirations: list[OptionExpirationSnapshot] = Field(default_factory=list)

    @property
    def contract_count(self) -> int:
        return len(self.contracts)


class GammaExposureSnapshot(BaseModel):
    """Derived gamma exposure row computed from canonical option snapshots."""

    underlying_symbol: str
    snapshot_ts: datetime
    expiration_date: date | None = None
    strike: float | None = None
    put_call: PutCall | None = None
    underlying_price: float
    gamma_exposure: float
    call_gamma_exposure: float | None = None
    put_gamma_exposure: float | None = None
    net_gamma_exposure: float | None = None
    open_interest: int | None = None
    volume: int | None = None
    contract_count: int | None = None
    aggregation_level: GammaAggregationLevel
    level_key: str
    methodology: str = "stockalert-schwab-gex-v1"
    source: str = "stockalert-schwab-gex"
    source_snapshot_id: str | None = None
    ingestion_ts: datetime | None = None
    ingestion_run_id: str | None = None

    @field_validator("underlying_symbol", mode="before")
    @classmethod
    def _normalize_upper(cls, value: str) -> str:
        return _upper(str(value))

    @field_validator("snapshot_ts", "ingestion_ts", mode="after")
    @classmethod
    def _normalize_ts(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None


class OptionSnapshotIngestResult(BaseModel):
    """Per-underlying result for one option-chain snapshot ingest."""

    symbol: str
    status: IngestStatus
    contracts_parsed: int = 0
    expirations_parsed: int = 0
    gamma_rows: int = 0
    rows_written: int = 0
    provider: str = "schwab"
    sink_status: str | None = None
    error: str | None = None
    snapshot_ts: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("symbol", mode="before")
    @classmethod
    def _normalize_symbol(cls, value: str) -> str:
        return _upper(str(value))

    @field_validator("snapshot_ts", mode="after")
    @classmethod
    def _normalize_ts(cls, value: datetime | None) -> datetime | None:
        return _utc(value) if value is not None else None
