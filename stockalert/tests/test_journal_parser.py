"""
Unit tests for `journal_parser.parse_transaction`.

These don't touch ClickHouse or HTTP — they exercise the pure parser against
real-shaped Schwab payloads (anonymized from a live `/transactions` response).
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.services.journal_parser import (
    FEE_TYPES,
    TradeRecord,
    parse_transaction,
)


HASH = "ACCTHASH_TEST"


def _sell_payload() -> dict:
    """A closing SELL of LIMN — modeled exactly on a real Schwab response."""
    return {
        "activityId": 114458158856,
        "time": "2026-03-16T18:58:47+0000",
        "accountNumber": "41903209",
        "type": "TRADE",
        "status": "VALID",
        "subAccount": "CASH",
        "tradeDate": "2026-03-16T18:58:47+0000",
        "positionId": 3190676192,
        "orderId": 1005717268527,
        "netAmount": 228.86,
        "transferItems": [
            {"instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"},
             "amount": 0.0, "cost": 0.0, "feeType": "COMMISSION"},
            {"instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"},
             "amount": 0.0, "cost": 0.0, "feeType": "SEC_FEE"},
            {"instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"},
             "amount": 0.0, "cost": 0.0, "feeType": "OPT_REG_FEE"},
            {"instrument": {"assetType": "CURRENCY", "symbol": "CURRENCY_USD"},
             "amount": 0.19, "cost": -0.19, "feeType": "TAF_FEE"},
            {"instrument": {"assetType": "EQUITY", "symbol": "LIMN",
                            "type": "COMMON_STOCK"},
             "amount": -950.0, "cost": 229.05, "price": 0.2411,
             "positionEffect": "CLOSING"},
        ],
    }


def _buy_payload() -> dict:
    """An opening BUY of RENX."""
    return {
        "activityId": 112121060190,
        "time": "2026-02-11T12:36:07+0000",
        "accountNumber": "41903209",
        "type": "TRADE",
        "status": "VALID",
        "tradeDate": "2026-02-11T12:36:07+0000",
        "positionId": 3185272642,
        "orderId": 1005395847284,
        "netAmount": -31.81,
        "transferItems": [
            {"instrument": {"assetType": "CURRENCY"}, "amount": 0.0,
             "cost": 0.0, "feeType": "COMMISSION"},
            {"instrument": {"assetType": "EQUITY", "symbol": "RENX"},
             "amount": 100.0, "cost": -31.81, "price": 0.3181,
             "positionEffect": "OPENING"},
        ],
    }


# ---------- Core parsing ----------


def test_parse_sell_extracts_correct_fields() -> None:
    rec = parse_transaction(_sell_payload(), account_hash=HASH)
    assert rec is not None
    assert isinstance(rec, TradeRecord)
    assert rec.account_hash == HASH
    assert rec.activity_id == 114458158856
    assert rec.order_id == 1005717268527
    assert rec.position_id == 3190676192
    assert rec.symbol == "LIMN"
    assert rec.asset_type == "EQUITY"
    assert rec.side == "SELL"
    assert rec.position_effect == "CLOSING"
    assert rec.quantity == 950.0
    assert rec.price == pytest.approx(0.2411)
    assert rec.gross_amount == pytest.approx(229.05)
    assert rec.fees == pytest.approx(0.19)
    assert rec.net_amount == pytest.approx(228.86)
    assert rec.status == "VALID"


def test_parse_buy_extracts_correct_fields() -> None:
    rec = parse_transaction(_buy_payload(), account_hash=HASH)
    assert rec is not None
    assert rec.symbol == "RENX"
    assert rec.side == "BUY"
    assert rec.position_effect == "OPENING"
    assert rec.quantity == 100.0
    assert rec.price == pytest.approx(0.3181)
    assert rec.net_amount == pytest.approx(-31.81)
    assert rec.fees == 0.0


def test_parse_time_in_utc() -> None:
    rec = parse_transaction(_sell_payload(), account_hash=HASH)
    assert rec is not None
    assert rec.trade_time == datetime(2026, 3, 16, 18, 58, 47, tzinfo=timezone.utc)


# ---------- Filtering / safety ----------


def test_non_trade_type_returns_none() -> None:
    payload = _sell_payload()
    payload["type"] = "DIVIDEND_OR_INTEREST"
    assert parse_transaction(payload, account_hash=HASH) is None


def test_missing_activity_id_returns_none() -> None:
    payload = _sell_payload()
    del payload["activityId"]
    assert parse_transaction(payload, account_hash=HASH) is None


def test_no_security_row_returns_none() -> None:
    """A pathological row with only fee buckets (and no fill) is dropped."""
    payload = _sell_payload()
    payload["transferItems"] = [
        it for it in payload["transferItems"] if "feeType" in it
    ]
    assert parse_transaction(payload, account_hash=HASH) is None


def test_non_dict_input_returns_none() -> None:
    assert parse_transaction(None, account_hash=HASH) is None  # type: ignore[arg-type]
    assert parse_transaction("not a dict", account_hash=HASH) is None  # type: ignore[arg-type]
    assert parse_transaction(42, account_hash=HASH) is None  # type: ignore[arg-type]


# ---------- Fee aggregation ----------


def test_fee_aggregation_sums_all_buckets() -> None:
    payload = _sell_payload()
    # Bump every fee bucket to a known non-zero value (via `cost`).
    for it in payload["transferItems"]:
        if it.get("feeType") == "COMMISSION":
            it["cost"] = -1.50
        elif it.get("feeType") == "SEC_FEE":
            it["cost"] = -0.03
        elif it.get("feeType") == "TAF_FEE":
            it["cost"] = -0.19
    rec = parse_transaction(payload, account_hash=HASH)
    assert rec is not None
    assert rec.fees == pytest.approx(1.50 + 0.03 + 0.19)


def test_fee_falls_back_to_amount_when_cost_zero() -> None:
    payload = _sell_payload()
    payload["transferItems"] = [
        {"instrument": {"assetType": "CURRENCY"},
         "amount": 0.65, "cost": 0.0, "feeType": "COMMISSION"},
        {"instrument": {"assetType": "EQUITY", "symbol": "X"},
         "amount": -1.0, "cost": 10.0, "price": 10.0,
         "positionEffect": "CLOSING"},
    ]
    rec = parse_transaction(payload, account_hash=HASH)
    assert rec is not None
    assert rec.fees == pytest.approx(0.65)


def test_known_fee_types_are_complete() -> None:
    """Sanity: our FEE_TYPES set covers the live ones we've observed."""
    for name in ("COMMISSION", "SEC_FEE", "TAF_FEE", "OPT_REG_FEE"):
        assert name in FEE_TYPES


# ---------- Side derivation ----------


def test_side_from_amount_sign_overrides_position_effect() -> None:
    """If `amount` is negative we say SELL even with a weird positionEffect."""
    payload = _buy_payload()
    sec = payload["transferItems"][-1]
    sec["amount"] = -100.0
    sec["positionEffect"] = "OPENING"  # nonsensical but possible in corp actions
    rec = parse_transaction(payload, account_hash=HASH)
    assert rec is not None
    assert rec.side == "SELL"
    assert rec.quantity == 100.0


def test_side_falls_back_to_position_effect_when_amount_zero() -> None:
    payload = _buy_payload()
    sec = payload["transferItems"][-1]
    sec["amount"] = 0.0
    sec["positionEffect"] = "CLOSING"
    rec = parse_transaction(payload, account_hash=HASH)
    assert rec is not None
    assert rec.side == "SELL"


# ---------- Raw JSON pass-through ----------


def test_raw_json_is_stored_when_provided() -> None:
    rec = parse_transaction(_sell_payload(), account_hash=HASH, raw_json='{"foo":1}')
    assert rec is not None
    assert rec.raw_json == '{"foo":1}'


def test_raw_json_empty_by_default() -> None:
    rec = parse_transaction(_sell_payload(), account_hash=HASH)
    assert rec is not None
    assert rec.raw_json == ""
