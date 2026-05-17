"""
HTTP API for the trading journal.

Endpoints:
  GET  /api/journal/accounts                  - latest snapshot per account
  GET  /api/journal/trades                    - list trades + notes
  GET  /api/journal/summary                   - realized P&L summary + daily/symbol breakdown
  PUT  /api/journal/notes/{activity_id}       - upsert strategy/tags/note on one trade
  POST /api/journal/sync                      - manual sync trigger

Provider-agnostic: never references SchwabProvider directly. All data comes
from ClickHouse via `journal_repo`; sync is delegated to `journal_sync_service`.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

from app.db import journal_repo
from app.services.journal.journal_sync import journal_sync_service
from app.services.journal.pnl import (
    compute_realized_pnl,
    overall_summary,
    summarize_by_day,
    summarize_by_symbol,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _ts(v) -> Optional[str]:
    """ISO-format with explicit UTC marker so JS Date() parses correctly."""
    if v is None:
        return None
    if hasattr(v, "isoformat"):
        if getattr(v, "tzinfo", None) is None:
            return v.isoformat() + "Z"
        return v.isoformat()
    return str(v)


@router.get("/journal/accounts")
async def list_accounts() -> dict:
    """
    Return the most-recent snapshot per linked account, masked so we never
    expose the real account number on the wire (only the hash + last-4).
    """
    snaps = await journal_repo.latest_snapshot_per_account_async()
    out = []
    for s in snaps:
        h = s["account_hash"]
        number = journal_sync_service.number_for_hash(h)
        masked = ("****" + number[-4:]) if number else "****"
        out.append({
            "account_hash": h,
            "account_label": masked,
            "snapshot_time": _ts(s.get("snapshot_time")),
            "account_type": s.get("account_type") or "",
            "is_day_trader": bool(s.get("is_day_trader")),
            "round_trips": int(s.get("round_trips") or 0),
            "cash_balance": float(s.get("cash_balance") or 0),
            "liquidation_value": float(s.get("liquidation_value") or 0),
            "long_market_value": float(s.get("long_market_value") or 0),
            "short_market_value": float(s.get("short_market_value") or 0),
            "buying_power": float(s.get("buying_power") or 0),
            "pending_deposits": float(s.get("pending_deposits") or 0),
        })
    return {"accounts": out}


@router.get("/journal/trades")
async def list_trades(
    account: Optional[str] = Query(None, description="account_hash, or omit for all"),
    symbol: Optional[str] = Query(None),
    days: int = Query(90, ge=1, le=730, description="Lookback window in days"),
    limit: int = Query(5000, ge=1, le=20_000),
) -> dict:
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows = await journal_repo.list_trades_async(
        account_hash=account, symbol=symbol, start=start, end=end, limit=limit,
    )
    out = []
    for r in rows:
        out.append({
            "account_hash": r["account_hash"],
            "activity_id": int(r["activity_id"]),
            "order_id": int(r["order_id"] or 0),
            "trade_time": _ts(r["trade_time"]),
            "symbol": r["symbol"],
            "asset_type": r["asset_type"],
            "side": r["side"],
            "position_effect": r["position_effect"],
            "quantity": float(r["quantity"]),
            "price": float(r["price"]),
            "gross_amount": float(r["gross_amount"]),
            "fees": float(r["fees"]),
            "net_amount": float(r["net_amount"]),
            "status": r["status"],
            "strategy": r.get("strategy") or "",
            "tags": list(r.get("tags") or []),
            "note": r.get("note") or "",
            "note_updated_at": _ts(r.get("note_updated_at")),
        })
    return {
        "window_days": days,
        "start": _ts(start), "end": _ts(end),
        "count": len(out),
        "trades": out,
    }


@router.get("/journal/summary")
async def journal_summary(
    account: Optional[str] = Query(None),
    days: int = Query(90, ge=1, le=730),
) -> dict:
    """
    Run FIFO P&L over the requested window and return:
      - overall: total realized P&L, win rate, etc.
      - by_day:   daily P&L bars
      - by_symbol: per-symbol rollup
      - legs: per-closing-trade P&L (small enough to send for <=5k trades)
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=days)
    rows = await journal_repo.list_trades_async(
        account_hash=account, start=start, end=end, limit=20_000,
    )
    # Trades come back DESC; FIFO needs ASC time order.
    rows.sort(key=lambda r: (r["trade_time"], r["activity_id"]))
    legs = compute_realized_pnl(rows)
    return {
        "window_days": days,
        "start": _ts(start), "end": _ts(end),
        "input_trade_count": len(rows),
        "overall": overall_summary(legs),
        "by_day": summarize_by_day(legs),
        "by_symbol": summarize_by_symbol(legs),
        "legs": [
            {
                "closing_activity_id": leg.closing_activity_id,
                "symbol": leg.symbol,
                "opened_at": _ts(leg.opened_at),
                "closed_at": _ts(leg.closed_at),
                "qty": leg.qty,
                "open_price": leg.open_price,
                "close_price": leg.close_price,
                "gross_pnl": leg.gross_pnl,
                "fees": leg.fees,
                "net_pnl": leg.net_pnl,
                "side": leg.side,
            }
            for leg in legs
        ],
    }


class NoteUpdate(BaseModel):
    account_hash: str = Field(..., description="Account hash from /api/journal/accounts")
    strategy: str = ""
    tags: list[str] = []
    note: str = ""


@router.put("/journal/notes/{activity_id}")
async def update_note(activity_id: int, body: NoteUpdate) -> dict:
    if not body.account_hash:
        raise HTTPException(400, "account_hash is required")
    await journal_repo.set_trade_note_async(
        account_hash=body.account_hash,
        activity_id=int(activity_id),
        strategy=body.strategy,
        tags=body.tags,
        note=body.note,
    )
    return {"ok": True, "activity_id": activity_id}


class SyncRequest(BaseModel):
    days: int = Field(30, ge=1, le=365)
    force: bool = True   # /sync is a manual button, default to bypassing throttle


@router.post("/journal/sync")
async def trigger_sync(req: Optional[SyncRequest] = None) -> dict:
    """
    Manually trigger a journal sync (account numbers + balances + trades).
    Returns count of accounts/snapshots/trades inserted, plus any per-account
    errors so the UI can surface them.
    """
    req = req or SyncRequest()
    journal_sync_service.TRADE_LOOKBACK_DAYS = max(1, int(req.days))
    out = await journal_sync_service.sync_all(force=req.force)
    return out
