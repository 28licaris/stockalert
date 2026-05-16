"""
Pure transaction → trade-row parser. Stateless, no I/O, easy to unit test.

The Schwab `/accounts/{hash}/transactions` endpoint returns objects shaped
roughly like:

    {
        "activityId": 114458158856,
        "time": "2026-03-16T18:58:47+0000",
        "accountNumber": "41903209",
        "type": "TRADE",
        "status": "VALID",
        "orderId": 1005717268527,
        "positionId": 3190676192,
        "netAmount": 228.86,
        "transferItems": [
            {"instrument": {"assetType": "CURRENCY", ...},
             "amount": 0.0, "cost": 0.0, "feeType": "COMMISSION"},
            ...more fee rows...,
            {"instrument": {"assetType": "EQUITY", "symbol": "LIMN", ...},
             "amount": -950.0, "cost": 229.05, "price": 0.2411,
             "positionEffect": "CLOSING"}
        ]
    }

`transferItems` is a polyglot list: each item is EITHER a fee bucket
(identified by `feeType`) OR the security fill (identified by
`positionEffect` + non-CURRENCY `assetType`). We extract the security row
as the trade and sum the fees.

`amount` sign convention from Schwab:
  - negative = shares LEAVING the account (sell)
  - positive = shares ENTERING the account (buy)
This is sometimes inverted in their docs vs reality — we derive `side` from
the sign of `amount`, falling back to `positionEffect`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional


# Fee-bucket items have one of these `feeType` values. We sum them into a
# single `fees` field on the trade. (TAF = trading activity fee, OPT_REG_FEE
# = options regulatory fee, etc.)
FEE_TYPES = {
    "COMMISSION",
    "SEC_FEE",
    "TAF_FEE",
    "OPT_REG_FEE",
    "INDEX_OPTION_FEE",
    "MISCELLANEOUS_FEE",
    "EXCHANGE_FEE",
    "CDSC_FEE",
}


@dataclass
class TradeRecord:
    """A single fill, normalized into our schema's column shape."""
    account_hash: str
    activity_id: int
    order_id: int = 0
    position_id: int = 0
    trade_time: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    symbol: str = ""
    asset_type: str = "EQUITY"
    side: str = ""               # "BUY" | "SELL" | ""
    position_effect: str = ""    # "OPENING" | "CLOSING" | ""
    quantity: float = 0.0        # abs share/contract count
    price: float = 0.0
    gross_amount: float = 0.0    # abs cost of the security row
    fees: float = 0.0
    net_amount: float = 0.0      # signed: + sell, - buy
    status: str = ""
    raw_json: str = ""


def _parse_iso(s: Optional[str]) -> datetime:
    """Tolerant ISO-8601 parser. Returns UTC-aware datetime."""
    if not s:
        return datetime.now(timezone.utc)
    # Schwab emits `+0000` (no colon); Python's fromisoformat needs `+00:00`.
    fixed = s.replace("Z", "+00:00")
    if len(fixed) >= 5 and fixed[-5] in ("+", "-") and fixed[-3] != ":":
        fixed = fixed[:-2] + ":" + fixed[-2:]
    try:
        dt = datetime.fromisoformat(fixed)
    except ValueError:
        return datetime.now(timezone.utc)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def parse_transaction(tx: dict, *, account_hash: str,
                       raw_json: Optional[str] = None) -> Optional[TradeRecord]:
    """
    Convert one Schwab transaction dict into a `TradeRecord`. Returns None
    for non-TRADE rows (dividends, ACH, etc.) or malformed payloads.
    """
    if not isinstance(tx, dict):
        return None
    if (tx.get("type") or "").upper() != "TRADE":
        return None
    activity_id = tx.get("activityId")
    if not activity_id:
        return None

    items = tx.get("transferItems") or []
    fees = 0.0
    security_row: Optional[dict] = None
    for it in items:
        if not isinstance(it, dict):
            continue
        fee_type = it.get("feeType")
        if fee_type:
            # Some payloads quote fees as positive `amount`, others as negative
            # `cost`. Use abs(cost) when present (signed by Schwab as outflow),
            # else fall back to abs(amount).
            cost = it.get("cost")
            amount = it.get("amount")
            if cost is not None and cost != 0:
                fees += abs(float(cost))
            elif amount is not None and amount != 0:
                fees += abs(float(amount))
            continue
        # The security row: any item that isn't a fee bucket.
        # Schwab places non-fee instrument fills here with `positionEffect`.
        inst = it.get("instrument") or {}
        # Skip pure currency adjustments without a positionEffect.
        if inst.get("assetType") == "CURRENCY" and not it.get("positionEffect"):
            continue
        security_row = it

    if security_row is None:
        return None

    inst = security_row.get("instrument") or {}
    symbol = (inst.get("symbol") or "").upper()
    asset_type = (inst.get("assetType") or "EQUITY").upper()

    # Derive side from amount sign first, fall back to positionEffect.
    amount_raw = security_row.get("amount")
    amount = float(amount_raw) if amount_raw is not None else 0.0
    pe = (security_row.get("positionEffect") or "").upper()
    if amount < 0:
        side = "SELL"
    elif amount > 0:
        side = "BUY"
    else:
        # Zero-amount: rare (corp actions, expirations). Use positionEffect.
        side = "SELL" if pe == "CLOSING" else "BUY" if pe == "OPENING" else ""

    qty = abs(amount)
    price_raw = security_row.get("price")
    price = float(price_raw) if price_raw is not None else 0.0
    cost_raw = security_row.get("cost")
    gross = abs(float(cost_raw)) if cost_raw is not None else qty * price

    net_amount_raw = tx.get("netAmount")
    net_amount = float(net_amount_raw) if net_amount_raw is not None else 0.0

    return TradeRecord(
        account_hash=account_hash,
        activity_id=int(activity_id),
        order_id=int(tx.get("orderId") or 0),
        position_id=int(tx.get("positionId") or 0),
        trade_time=_parse_iso(tx.get("tradeDate") or tx.get("time")),
        symbol=symbol,
        asset_type=asset_type,
        side=side,
        position_effect=pe,
        quantity=qty,
        price=price,
        gross_amount=gross,
        fees=round(fees, 6),
        net_amount=net_amount,
        status=(tx.get("status") or "").upper(),
        raw_json=raw_json or "",
    )
