"""
Pure FIFO realized P&L calculation. No I/O, no clock — just inputs in,
outputs out, so it's trivial to unit-test against canned trade sequences.

Algorithm:
  1. Group trades by (account_hash, symbol).
  2. Within each group, sort chronologically.
  3. Maintain a FIFO queue of open lots `[(qty_remaining, price, time, trade)]`.
     - BUY  -> push a new lot
     - SELL -> pop lots until SELL qty is consumed; record realized for each match
  4. Sum proportional fees from both the opening and closing trade for
     each match so they're not double-counted across multiple partial fills.

This is the standard tax-style FIFO. Short selling is supported in the
opposite direction (open a short on SELL, cover on BUY).

Output:
  - `realized_pnl_per_trade`: list of dict per CLOSING (or covering) fill with
    {activity_id, account_hash, symbol, closed_at, qty, gross_pnl, fees, net_pnl}.
    The activity_id matches the closing trade so the UI can attach P&L to it.
  - `realized_pnl_by_day`: list of {date, net_pnl, gross_pnl, trade_count}.
  - `realized_pnl_by_symbol`: list of {symbol, net_pnl, gross_pnl, trade_count,
    win_count, loss_count}.
  - `summary`: rollups (total_realized_pnl, win_rate, gross_pnl, fees,
    closed_trade_count, winners, losers).
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Iterable


@dataclass
class _Lot:
    """One open position lot, awaiting a match."""
    qty_remaining: float
    price: float
    opened_at: datetime
    opening_fees: float       # total fees on the opening fill
    opening_qty: float        # full size of the opening fill (for fee proration)
    side: str                 # "LONG" or "SHORT"


@dataclass
class PnLLeg:
    """One realized leg = one match between an opening and a closing fill."""
    account_hash: str
    symbol: str
    closing_activity_id: int
    opening_activity_id: int
    closed_at: datetime
    opened_at: datetime
    qty: float                # matched qty (always positive)
    open_price: float
    close_price: float
    gross_pnl: float          # (close - open) * qty, sign-correct for long/short
    fees: float               # prorated fees from BOTH sides
    net_pnl: float            # gross_pnl - fees
    side: str                 # "LONG" or "SHORT" (the original lot's side)


def _trade_sort_key(t: dict) -> tuple:
    # Stable sort on (trade_time, activity_id) so partial fills at the same
    # second still process in a deterministic order.
    return (t.get("trade_time"), t.get("activity_id", 0))


def compute_realized_pnl(trades: Iterable[dict]) -> list[PnLLeg]:
    """
    Walk all trades FIFO per (account_hash, symbol) and emit realized legs.

    Input trades are dicts with at least:
        account_hash, activity_id, trade_time, symbol, side,
        quantity, price, fees, position_effect (optional)

    Side conventions:
        "BUY" + no open short -> open a LONG lot
        "SELL" against open longs -> close those longs (FIFO)
        "SELL" with no open longs -> open a SHORT lot
        "BUY" against open shorts -> cover those shorts (FIFO)

    Fees are prorated against the matched qty / original fill size so partial
    closes don't double-count fees.
    """
    by_key: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for t in trades:
        key = (t.get("account_hash", ""), (t.get("symbol") or "").upper())
        by_key[key].append(t)

    legs: list[PnLLeg] = []
    for (acct, sym), group in by_key.items():
        group.sort(key=_trade_sort_key)
        lots: deque[_Lot] = deque()
        for t in group:
            side = (t.get("side") or "").upper()
            qty = float(t.get("quantity") or 0.0)
            if qty <= 0:
                continue
            price = float(t.get("price") or 0.0)
            fees = float(t.get("fees") or 0.0)
            ts = t.get("trade_time") or datetime.min
            aid = int(t.get("activity_id") or 0)

            # Determine if this fill closes existing lots or opens new ones.
            # We close when the fill's direction is opposite the front-most
            # lot. If there are no lots, we open in the side's natural
            # direction (BUY -> LONG, SELL -> SHORT).
            while qty > 0 and lots and (
                (side == "SELL" and lots[0].side == "LONG") or
                (side == "BUY"  and lots[0].side == "SHORT")
            ):
                lot = lots[0]
                matched = min(qty, lot.qty_remaining)

                # Prorate fees: closing fill's fees split by matched/qty_in_this_fill,
                # opening fill's fees split by matched/opening_qty.
                # We multiply CLOSING side after the loop because we need the
                # full closing fill qty to prorate; do it via end-of-loop:
                lot_fees_share = (lot.opening_fees * (matched / lot.opening_qty)) if lot.opening_qty else 0.0

                if lot.side == "LONG":
                    gross = (price - lot.price) * matched
                else:
                    gross = (lot.price - price) * matched

                legs.append(PnLLeg(
                    account_hash=acct,
                    symbol=sym,
                    closing_activity_id=aid,
                    opening_activity_id=0,  # populated below
                    closed_at=ts,
                    opened_at=lot.opened_at,
                    qty=matched,
                    open_price=lot.price,
                    close_price=price,
                    gross_pnl=gross,
                    fees=lot_fees_share,    # closing side prorated after the loop
                    net_pnl=gross - lot_fees_share,
                    side=lot.side,
                ))
                lot.qty_remaining -= matched
                qty -= matched
                if lot.qty_remaining <= 1e-9:
                    lots.popleft()

            # Whatever wasn't used for closing opens a new lot in the natural
            # direction.
            if qty > 0:
                new_side = "LONG" if side == "BUY" else "SHORT"
                lots.append(_Lot(
                    qty_remaining=qty,
                    price=price,
                    opened_at=ts,
                    opening_fees=fees,
                    opening_qty=qty,
                    side=new_side,
                ))

            # Add the closing-side fee share to legs from this fill only.
            # We tag the most recently appended legs that share `closing_activity_id == aid`.
            # Closing fee prorated against the ORIGINAL qty of this fill (not what's left).
            full_qty = float(t.get("quantity") or 0.0)
            if fees > 0 and side in ("SELL", "BUY") and full_qty > 0:
                for leg in reversed(legs):
                    if leg.closing_activity_id != aid:
                        break
                    share = fees * (leg.qty / full_qty)
                    leg.fees = round(leg.fees + share, 6)
                    leg.net_pnl = round(leg.gross_pnl - leg.fees, 6)

    return legs


# ---------- Summaries ----------


def summarize_by_day(legs: Iterable[PnLLeg]) -> list[dict]:
    """Per-day realized P&L for an equity-curve / daily bar chart."""
    out: dict[date, dict] = defaultdict(lambda: {
        "date": None, "net_pnl": 0.0, "gross_pnl": 0.0,
        "fees": 0.0, "trade_count": 0,
    })
    for leg in legs:
        d = leg.closed_at.date() if hasattr(leg.closed_at, "date") else date.today()
        row = out[d]
        row["date"] = d.isoformat()
        row["net_pnl"] += leg.net_pnl
        row["gross_pnl"] += leg.gross_pnl
        row["fees"] += leg.fees
        row["trade_count"] += 1
    return sorted(out.values(), key=lambda r: r["date"])


def summarize_by_symbol(legs: Iterable[PnLLeg]) -> list[dict]:
    """Per-symbol P&L summary for the dashboard table."""
    out: dict[str, dict] = defaultdict(lambda: {
        "symbol": "", "net_pnl": 0.0, "gross_pnl": 0.0,
        "fees": 0.0, "trade_count": 0, "win_count": 0, "loss_count": 0,
    })
    for leg in legs:
        row = out[leg.symbol]
        row["symbol"] = leg.symbol
        row["net_pnl"] += leg.net_pnl
        row["gross_pnl"] += leg.gross_pnl
        row["fees"] += leg.fees
        row["trade_count"] += 1
        if leg.net_pnl > 0:
            row["win_count"] += 1
        elif leg.net_pnl < 0:
            row["loss_count"] += 1
    return sorted(out.values(), key=lambda r: -r["net_pnl"])


def overall_summary(legs: list[PnLLeg]) -> dict:
    """Top-line KPI row: total P&L, win rate, gross/fees split."""
    if not legs:
        return {
            "total_realized_pnl": 0.0,
            "gross_pnl": 0.0,
            "fees": 0.0,
            "closed_trade_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "win_rate": 0.0,
            "avg_winner": 0.0,
            "avg_loser": 0.0,
        }
    net = sum(l.net_pnl for l in legs)
    gross = sum(l.gross_pnl for l in legs)
    fees = sum(l.fees for l in legs)
    winners = [l for l in legs if l.net_pnl > 0]
    losers  = [l for l in legs if l.net_pnl < 0]
    return {
        "total_realized_pnl": round(net, 6),
        "gross_pnl": round(gross, 6),
        "fees": round(fees, 6),
        "closed_trade_count": len(legs),
        "win_count": len(winners),
        "loss_count": len(losers),
        "win_rate": round(len(winners) / len(legs), 4) if legs else 0.0,
        "avg_winner": round(sum(l.net_pnl for l in winners) / len(winners), 6) if winners else 0.0,
        "avg_loser": round(sum(l.net_pnl for l in losers) / len(losers), 6) if losers else 0.0,
    }
