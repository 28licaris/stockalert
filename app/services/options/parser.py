"""Schwab option-chain parsing and derived gamma exposure calculations."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, timezone
from typing import Any, Iterable

from app.services.options.schemas import (
    GammaExposureSnapshot,
    OptionChainParseResult,
    OptionChainRawSnapshot,
    OptionContractSnapshot,
    OptionExpirationSnapshot,
    PutCall,
)


def parse_schwab_option_chain(
    payload: dict[str, Any],
    *,
    snapshot_ts: datetime,
    request_params: dict[str, Any] | None = None,
    ingestion_run_id: str | None = None,
) -> OptionChainParseResult:
    """Normalize one Schwab `/chains` response into canonical DTOs."""
    if not isinstance(payload, dict):
        raise ValueError("Schwab option chain payload must be a dict")

    snapshot_ts = _as_utc(snapshot_ts)
    ingestion_ts = datetime.now(timezone.utc)
    underlying_symbol = _underlying_symbol(payload, request_params)
    underlying_price = _number(
        payload.get("underlyingPrice"),
        _nested(payload, "underlying", "last"),
        _nested(payload, "underlying", "mark"),
    )

    raw = OptionChainRawSnapshot(
        underlying_symbol=underlying_symbol,
        snapshot_ts=snapshot_ts,
        request_params=request_params or {},
        status=str(payload.get("status") or ""),
        is_delayed=_bool(payload.get("isDelayed")),
        underlying_price=underlying_price,
        raw_payload=payload,
        ingestion_ts=ingestion_ts,
        ingestion_run_id=ingestion_run_id,
    )

    contracts = [
        contract
        for side, exp_map in (
            ("CALL", payload.get("callExpDateMap")),
            ("PUT", payload.get("putExpDateMap")),
        )
        for contract in _parse_side(
            side=side,
            exp_map=exp_map,
            underlying_symbol=underlying_symbol,
            underlying_price=underlying_price,
            snapshot_ts=snapshot_ts,
            ingestion_ts=ingestion_ts,
            ingestion_run_id=ingestion_run_id,
        )
    ]

    expirations = _expirations_from_contracts(
        contracts,
        observed_ts=snapshot_ts,
        ingestion_ts=ingestion_ts,
        ingestion_run_id=ingestion_run_id,
    )

    return OptionChainParseResult(
        raw_snapshot=raw,
        contracts=contracts,
        expirations=expirations,
    )


def contract_gamma_exposure(contract: OptionContractSnapshot) -> float | None:
    """Signed dollar gamma exposure for a 1% underlying move."""
    if (
        contract.gamma is None
        or contract.open_interest is None
        or contract.underlying_price is None
    ):
        return None
    multiplier = contract.multiplier or 100.0
    unsigned = (
        contract.gamma
        * contract.open_interest
        * multiplier
        * contract.underlying_price
        * 0.01
        * contract.underlying_price
    )
    return unsigned if contract.put_call == "CALL" else -unsigned


def aggregate_gamma_exposure(
    contracts: Iterable[OptionContractSnapshot],
    *,
    source_snapshot_id: str | None = None,
    methodology: str = "stockalert-schwab-gex-v1",
    ingestion_run_id: str | None = None,
) -> list[GammaExposureSnapshot]:
    """Aggregate contract GEX into total, strike, expiry, and strike-expiry rows."""
    usable: list[tuple[OptionContractSnapshot, float]] = []
    for contract in contracts:
        exposure = contract_gamma_exposure(contract)
        if exposure is not None:
            usable.append((contract, exposure))
    if not usable:
        return []

    rows: list[GammaExposureSnapshot] = []
    rows.append(
        _gamma_row(
            usable,
            aggregation_level="total",
            level_key="total",
            source_snapshot_id=source_snapshot_id,
            methodology=methodology,
            ingestion_run_id=ingestion_run_id,
        )
    )

    for strike, group in _group_by(usable, lambda item: item[0].strike).items():
        rows.append(
            _gamma_row(
                group,
                aggregation_level="strike",
                level_key=f"strike:{strike:g}",
                strike=strike,
                source_snapshot_id=source_snapshot_id,
                methodology=methodology,
                ingestion_run_id=ingestion_run_id,
            )
        )

    for expiration_date, group in _group_by(usable, lambda item: item[0].expiration_date).items():
        rows.append(
            _gamma_row(
                group,
                aggregation_level="expiry",
                level_key=f"expiry:{expiration_date.isoformat()}",
                expiration_date=expiration_date,
                source_snapshot_id=source_snapshot_id,
                methodology=methodology,
                ingestion_run_id=ingestion_run_id,
            )
        )

    for (expiration_date, strike), group in _group_by(
        usable, lambda item: (item[0].expiration_date, item[0].strike)
    ).items():
        rows.append(
            _gamma_row(
                group,
                aggregation_level="strike_expiry",
                level_key=f"strike_expiry:{expiration_date.isoformat()}:{strike:g}",
                expiration_date=expiration_date,
                strike=strike,
                source_snapshot_id=source_snapshot_id,
                methodology=methodology,
                ingestion_run_id=ingestion_run_id,
            )
        )

    return rows


def _parse_side(
    *,
    side: PutCall,
    exp_map: Any,
    underlying_symbol: str,
    underlying_price: float | None,
    snapshot_ts: datetime,
    ingestion_ts: datetime,
    ingestion_run_id: str | None,
) -> list[OptionContractSnapshot]:
    if not isinstance(exp_map, dict):
        return []

    contracts: list[OptionContractSnapshot] = []
    for exp_key, strikes in exp_map.items():
        if not isinstance(strikes, dict):
            continue
        exp_date = _expiration_date(exp_key)
        days_to_exp = _days_to_expiration(exp_key)
        for strike_key, contract_list in strikes.items():
            if not isinstance(contract_list, list):
                continue
            for raw in contract_list:
                if not isinstance(raw, dict):
                    continue
                contract = _contract_from_raw(
                    raw,
                    side=side,
                    fallback_strike=_number(strike_key),
                    fallback_expiration=exp_date,
                    fallback_days_to_expiration=days_to_exp,
                    underlying_symbol=underlying_symbol,
                    underlying_price=underlying_price,
                    snapshot_ts=snapshot_ts,
                    ingestion_ts=ingestion_ts,
                    ingestion_run_id=ingestion_run_id,
                )
                contracts.append(contract)
    return contracts


def _contract_from_raw(
    raw: dict[str, Any],
    *,
    side: PutCall,
    fallback_strike: float | None,
    fallback_expiration: date,
    fallback_days_to_expiration: int | None,
    underlying_symbol: str,
    underlying_price: float | None,
    snapshot_ts: datetime,
    ingestion_ts: datetime,
    ingestion_run_id: str | None,
) -> OptionContractSnapshot:
    return OptionContractSnapshot(
        underlying_symbol=underlying_symbol,
        option_symbol=str(raw.get("symbol") or raw.get("description") or "").strip(),
        snapshot_ts=snapshot_ts,
        put_call=str(raw.get("putCall") or side).upper(),
        expiration_date=_contract_expiration_date(raw.get("expirationDate"), fallback_expiration),
        strike=_number(raw.get("strikePrice"), fallback_strike) or 0.0,
        underlying_price=underlying_price,
        days_to_expiration=_integer(raw.get("daysToExpiration"), fallback_days_to_expiration),
        bid=_number(raw.get("bidPrice")),
        ask=_number(raw.get("askPrice")),
        last=_number(raw.get("lastPrice")),
        mark=_number(raw.get("markPrice")),
        bid_size=_integer(raw.get("bidSize")),
        ask_size=_integer(raw.get("askSize")),
        last_size=_integer(raw.get("lastSize")),
        volume=_integer(raw.get("totalVolume")),
        open_interest=_integer(raw.get("openInterest")),
        quote_time=_epoch_ms(raw.get("quoteTimeInLong")),
        trade_time=_epoch_ms(raw.get("tradeTimeInLong")),
        delta=_number(raw.get("delta")),
        gamma=_number(raw.get("gamma")),
        theta=_number(raw.get("theta")),
        vega=_number(raw.get("vega")),
        rho=_number(raw.get("rho")),
        volatility=_number(raw.get("volatility")),
        theoretical_value=_number(raw.get("theoreticalOptionValue")),
        intrinsic_value=_number(raw.get("intrinsicValue")),
        time_value=_number(raw.get("timeValue")),
        in_the_money=_bool(raw.get("isInTheMoney")),
        mini=_bool(raw.get("isMini")),
        non_standard=_bool(raw.get("isNonStandard")),
        penny_pilot=_bool(raw.get("isPennyPilot")),
        multiplier=_number(raw.get("multiplier")),
        settlement_type=_optional_str(raw.get("settlementType")),
        expiration_type=_optional_str(raw.get("expirationType")),
        ingestion_ts=ingestion_ts,
        ingestion_run_id=ingestion_run_id,
    )


def _gamma_row(
    group: list[tuple[OptionContractSnapshot, float]],
    *,
    aggregation_level: str,
    level_key: str,
    source_snapshot_id: str | None,
    methodology: str,
    ingestion_run_id: str | None,
    expiration_date: date | None = None,
    strike: float | None = None,
) -> GammaExposureSnapshot:
    first = group[0][0]
    call_exposure = sum(exposure for contract, exposure in group if contract.put_call == "CALL")
    put_exposure = sum(exposure for contract, exposure in group if contract.put_call == "PUT")
    net_exposure = call_exposure + put_exposure
    return GammaExposureSnapshot(
        underlying_symbol=first.underlying_symbol,
        snapshot_ts=first.snapshot_ts,
        expiration_date=expiration_date,
        strike=strike,
        underlying_price=first.underlying_price or 0.0,
        gamma_exposure=net_exposure,
        call_gamma_exposure=call_exposure,
        put_gamma_exposure=put_exposure,
        net_gamma_exposure=net_exposure,
        open_interest=sum(contract.open_interest or 0 for contract, _ in group),
        volume=sum(contract.volume or 0 for contract, _ in group),
        contract_count=len(group),
        aggregation_level=aggregation_level,
        level_key=level_key,
        methodology=methodology,
        source_snapshot_id=source_snapshot_id,
        ingestion_ts=datetime.now(timezone.utc),
        ingestion_run_id=ingestion_run_id,
    )


def _expirations_from_contracts(
    contracts: list[OptionContractSnapshot],
    *,
    observed_ts: datetime,
    ingestion_ts: datetime,
    ingestion_run_id: str | None,
) -> list[OptionExpirationSnapshot]:
    by_key: dict[tuple[str, date], OptionContractSnapshot] = {}
    for contract in contracts:
        by_key.setdefault(
            (contract.underlying_symbol, contract.expiration_date), contract
        )
    return [
        OptionExpirationSnapshot(
            underlying_symbol=symbol,
            expiration_date=expiration_date,
            days_to_expiration=contract.days_to_expiration,
            expiration_type=contract.expiration_type,
            settlement_type=contract.settlement_type,
            observed_ts=observed_ts,
            ingestion_ts=ingestion_ts,
            ingestion_run_id=ingestion_run_id,
        )
        for (symbol, expiration_date), contract in sorted(by_key.items(), key=lambda item: item[0])
    ]


def _underlying_symbol(payload: dict[str, Any], request_params: dict[str, Any] | None) -> str:
    value = (
        payload.get("symbol")
        or _nested(payload, "underlying", "symbol")
        or (request_params or {}).get("symbol")
    )
    if not value:
        raise ValueError("Schwab option chain payload missing underlying symbol")
    return str(value).strip().upper()


def _group_by(items: list[tuple[OptionContractSnapshot, float]], key_fn):
    grouped = defaultdict(list)
    for item in items:
        grouped[key_fn(item)].append(item)
    return grouped


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _nested(payload: dict[str, Any], *keys: str) -> Any:
    cur: Any = payload
    for key in keys:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(key)
    return cur


def _number(*values: Any) -> float | None:
    for value in values:
        if value is None or value == "":
            continue
        try:
            return float(value)
        except (TypeError, ValueError):
            continue
    return None


def _integer(*values: Any) -> int | None:
    number = _number(*values)
    return int(number) if number is not None else None


def _bool(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"true", "1", "yes"}:
            return True
        if lowered in {"false", "0", "no"}:
            return False
    return bool(value)


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _expiration_date(exp_key: str) -> date:
    raw = str(exp_key).split(":", 1)[0]
    return date.fromisoformat(raw)


def _days_to_expiration(exp_key: str) -> int | None:
    parts = str(exp_key).split(":", 1)
    if len(parts) != 2:
        return None
    return _integer(parts[1])


def _contract_expiration_date(value: Any, fallback: date) -> date:
    if value in (None, ""):
        return fallback
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value)
    if "T" in text:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).date()
    return date.fromisoformat(text[:10])


def _epoch_ms(value: Any) -> datetime | None:
    ms = _integer(value)
    if ms is None or ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=timezone.utc)
