from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.services.options.parser import (
    aggregate_gamma_exposure,
    contract_gamma_exposure,
    parse_schwab_option_chain,
)
from app.services.options.schemas import OptionContractSnapshot


FIXTURE = Path(__file__).parent / "fixtures" / "schwab_chain_aapl.json"


def _payload() -> dict:
    return json.loads(FIXTURE.read_text())


def test_parse_schwab_chain_normalizes_contracts_and_raw_snapshot() -> None:
    result = parse_schwab_option_chain(
        _payload(),
        snapshot_ts=datetime(2026, 6, 27, 14, 30),
        request_params={"symbol": "aapl", "strikeCount": 20},
        ingestion_run_id="run-1",
    )

    assert result.raw_snapshot.underlying_symbol == "AAPL"
    assert result.raw_snapshot.snapshot_ts.tzinfo == timezone.utc
    assert result.raw_snapshot.provider == "schwab"
    assert result.raw_snapshot.underlying_price == 210.5
    assert result.contract_count == 3
    assert {contract.put_call for contract in result.contracts} == {"CALL", "PUT"}
    assert {contract.expiration_date.isoformat() for contract in result.contracts} == {"2026-07-17"}

    call = next(contract for contract in result.contracts if contract.option_symbol.endswith("C00210000"))
    assert call.underlying_symbol == "AAPL"
    assert call.snapshot_ts.tzinfo == timezone.utc
    assert call.strike == 210.0
    assert call.bid == 8.4
    assert call.ask == 8.55
    assert call.open_interest == 2500
    assert call.gamma == 0.042
    assert call.quote_time == datetime(2026, 7, 17, 17, 30, tzinfo=timezone.utc)
    assert call.ingestion_run_id == "run-1"


def test_parse_schwab_chain_builds_unique_expirations() -> None:
    result = parse_schwab_option_chain(
        _payload(),
        snapshot_ts=datetime(2026, 6, 27, 14, 30, tzinfo=timezone.utc),
    )

    assert len(result.expirations) == 1
    expiration = result.expirations[0]
    assert expiration.underlying_symbol == "AAPL"
    assert expiration.expiration_date.isoformat() == "2026-07-17"
    assert expiration.days_to_expiration == 20
    assert expiration.expiration_type == "W"


def test_parse_empty_chain_logs_shape_as_zero_contract_result() -> None:
    result = parse_schwab_option_chain(
        {"symbol": "MSFT", "status": "SUCCESS", "callExpDateMap": {}, "putExpDateMap": {}},
        snapshot_ts=datetime(2026, 6, 27, 14, 30, tzinfo=timezone.utc),
    )

    assert result.raw_snapshot.underlying_symbol == "MSFT"
    assert result.contracts == []
    assert result.expirations == []


def test_parse_requires_underlying_symbol() -> None:
    with pytest.raises(ValueError, match="missing underlying symbol"):
        parse_schwab_option_chain({}, snapshot_ts=datetime.now(timezone.utc))


def test_contract_schema_rejects_bad_strike() -> None:
    with pytest.raises(ValidationError, match="strike must be positive"):
        OptionContractSnapshot(
            underlying_symbol="aapl",
            option_symbol="AAPL  260717C00000000",
            snapshot_ts=datetime.now(timezone.utc),
            put_call="CALL",
            expiration_date="2026-07-17",
            strike=0,
        )


def test_contract_gamma_exposure_signs_calls_positive_and_puts_negative() -> None:
    result = parse_schwab_option_chain(
        _payload(),
        snapshot_ts=datetime(2026, 6, 27, 14, 30, tzinfo=timezone.utc),
    )
    call = next(contract for contract in result.contracts if contract.put_call == "CALL")
    put = next(contract for contract in result.contracts if contract.put_call == "PUT")

    call_gex = contract_gamma_exposure(call)
    put_gex = contract_gamma_exposure(put)

    assert call_gex == pytest.approx(0.042 * 2500 * 100 * 210.5 * 0.01 * 210.5)
    assert put_gex == pytest.approx(-(0.041 * 3100 * 100 * 210.5 * 0.01 * 210.5))


def test_aggregate_gamma_exposure_outputs_replayable_levels() -> None:
    result = parse_schwab_option_chain(
        _payload(),
        snapshot_ts=datetime(2026, 6, 27, 14, 30, tzinfo=timezone.utc),
        ingestion_run_id="run-1",
    )

    rows = aggregate_gamma_exposure(
        result.contracts,
        source_snapshot_id="snapshot-123",
        ingestion_run_id="run-1",
    )

    total = next(row for row in rows if row.aggregation_level == "total")
    strike_210 = next(
        row for row in rows
        if row.aggregation_level == "strike" and row.strike == 210.0
    )
    assert {row.aggregation_level for row in rows} == {
        "total",
        "strike",
        "expiry",
        "strike_expiry",
    }
    assert total.source_snapshot_id == "snapshot-123"
    assert total.level_key == "total"
    assert total.contract_count == 3
    assert total.open_interest == 7500
    assert total.call_gamma_exposure is not None and total.call_gamma_exposure > 0
    assert total.put_gamma_exposure is not None and total.put_gamma_exposure < 0
    assert total.net_gamma_exposure == pytest.approx(total.gamma_exposure)
    assert strike_210.contract_count == 2
    assert strike_210.level_key == "strike:210"


def test_parses_schwab_quote_fields_and_tolerates_the_legacy_tda_spelling() -> None:
    """Regression: every option quote was silently NULL from first ingest.

    The parser read `bidPrice`/`askPrice`/`lastPrice`/`markPrice` — TD
    Ameritrade's legacy schema. Schwab's `/chains` sends `bid`/`ask`/`last`/
    `mark`, so `_number` returned None for all four on every contract, forever.
    It went unnoticed because the fixture was written with the same wrong names,
    so the test agreed with the bug. Verified against stored `schwab_chain_raw`.

    Both spellings are accepted now; this pins each one independently so the
    real-name path cannot silently rot back to the fallback.
    """
    def chain(contract: dict) -> dict:
        return {
            "symbol": "AAPL",
            "underlyingPrice": 100.0,
            "callExpDateMap": {"2026-09-18:30": {"100.0": [
                {"symbol": "AAPL  260918C00100000", "putCall": "CALL",
                 "strikePrice": 100.0, "expirationDate": "2026-09-18T00:00:00.000+00:00",
                 "daysToExpiration": 30, "volatility": 22.5, **contract},
            ]}},
        }

    ts = datetime(2026, 8, 9, 15, 30, tzinfo=timezone.utc)

    # Schwab's real spelling — the one that matters in production.
    live = parse_schwab_option_chain(
        chain({"bid": 4.10, "ask": 4.30, "last": 4.20, "mark": 4.20}), snapshot_ts=ts
    ).contracts[0]
    assert (live.bid, live.ask, live.last, live.mark) == (4.10, 4.30, 4.20, 4.20)

    # Legacy TDA spelling still parses, so an older captured payload replays.
    legacy = parse_schwab_option_chain(
        chain({"bidPrice": 1.10, "askPrice": 1.30, "lastPrice": 1.20, "markPrice": 1.25}),
        snapshot_ts=ts,
    ).contracts[0]
    assert (legacy.bid, legacy.ask, legacy.last, legacy.mark) == (1.10, 1.30, 1.20, 1.25)

    # A contract with neither spelling must yield None, not 0.0 — a zero bid is
    # a tradeable fact and must never be manufactured from missing data.
    absent = parse_schwab_option_chain(chain({}), snapshot_ts=ts).contracts[0]
    assert absent.bid is None and absent.ask is None and absent.mark is None
