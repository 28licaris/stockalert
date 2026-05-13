"""
Unit tests for the FIFO realized P&L engine. Pure-function — no DB, no HTTP.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.pnl import (
    compute_realized_pnl,
    overall_summary,
    summarize_by_day,
    summarize_by_symbol,
)


ACC = "ACCTHASH"
T0 = datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc)


def trade(
    activity_id: int,
    *,
    side: str,
    qty: float,
    price: float,
    symbol: str = "TEST",
    fees: float = 0.0,
    minute: int = 0,
    account: str = ACC,
) -> dict:
    return {
        "account_hash": account,
        "activity_id": activity_id,
        "trade_time": T0 + timedelta(minutes=minute),
        "symbol": symbol,
        "side": side.upper(),
        "quantity": qty,
        "price": price,
        "fees": fees,
    }


# ---------- LONG-side trades ----------


def test_simple_long_round_trip() -> None:
    legs = compute_realized_pnl([
        trade(1, side="BUY",  qty=100, price=10.0),
        trade(2, side="SELL", qty=100, price=12.0, minute=10),
    ])
    assert len(legs) == 1
    leg = legs[0]
    assert leg.symbol == "TEST"
    assert leg.qty == 100
    assert leg.open_price == 10.0
    assert leg.close_price == 12.0
    assert leg.gross_pnl == pytest.approx(200.0)
    assert leg.fees == 0.0
    assert leg.net_pnl == pytest.approx(200.0)
    assert leg.side == "LONG"


def test_long_loss() -> None:
    legs = compute_realized_pnl([
        trade(1, side="BUY",  qty=10, price=100.0),
        trade(2, side="SELL", qty=10, price=80.0, minute=5),
    ])
    assert legs[0].gross_pnl == pytest.approx(-200.0)


def test_partial_close_keeps_residual_lot() -> None:
    """BUY 100 then SELL 40 -> 1 leg, 60 still open, no further P&L."""
    legs = compute_realized_pnl([
        trade(1, side="BUY",  qty=100, price=10.0),
        trade(2, side="SELL", qty=40,  price=11.0, minute=5),
    ])
    assert len(legs) == 1
    assert legs[0].qty == 40
    assert legs[0].gross_pnl == pytest.approx(40.0)


def test_multiple_partial_buys_close_at_once_fifo() -> None:
    """Two BUYs at different prices, one SELL closing both -> 2 legs FIFO."""
    legs = compute_realized_pnl([
        trade(1, side="BUY",  qty=50, price=10.0, minute=0),
        trade(2, side="BUY",  qty=50, price=20.0, minute=5),
        trade(3, side="SELL", qty=100, price=25.0, minute=10),
    ])
    assert len(legs) == 2
    # FIFO: first leg matches the qty=50 @ 10 lot
    assert legs[0].qty == 50
    assert legs[0].open_price == 10.0
    assert legs[0].gross_pnl == pytest.approx(15.0 * 50)  # = 750
    # Second leg matches the qty=50 @ 20 lot
    assert legs[1].qty == 50
    assert legs[1].open_price == 20.0
    assert legs[1].gross_pnl == pytest.approx(5.0 * 50)   # = 250
    # Both share the same closing fill -> same closing_activity_id
    assert legs[0].closing_activity_id == legs[1].closing_activity_id == 3


def test_one_buy_closed_across_two_sells() -> None:
    """BUY 100 -> SELL 60 -> SELL 40 -> 2 legs."""
    legs = compute_realized_pnl([
        trade(1, side="BUY",  qty=100, price=10.0),
        trade(2, side="SELL", qty=60,  price=12.0, minute=5),
        trade(3, side="SELL", qty=40,  price=11.0, minute=10),
    ])
    assert len(legs) == 2
    assert legs[0].qty == 60
    assert legs[0].gross_pnl == pytest.approx(120.0)
    assert legs[0].closing_activity_id == 2
    assert legs[1].qty == 40
    assert legs[1].gross_pnl == pytest.approx(40.0)
    assert legs[1].closing_activity_id == 3


# ---------- SHORT-side trades ----------


def test_short_round_trip() -> None:
    """SELL 100 @ 50, COVER 100 @ 40 -> +1000 gross."""
    legs = compute_realized_pnl([
        trade(1, side="SELL", qty=100, price=50.0),
        trade(2, side="BUY",  qty=100, price=40.0, minute=5),
    ])
    assert len(legs) == 1
    assert legs[0].side == "SHORT"
    assert legs[0].gross_pnl == pytest.approx(1000.0)


def test_short_loss() -> None:
    """SELL 10 @ 50, COVER 10 @ 60 -> -100 gross."""
    legs = compute_realized_pnl([
        trade(1, side="SELL", qty=10, price=50.0),
        trade(2, side="BUY",  qty=10, price=60.0, minute=5),
    ])
    assert legs[0].gross_pnl == pytest.approx(-100.0)


# ---------- Fees ----------


def test_fees_are_subtracted_proportionally() -> None:
    """Fees on both sides should reduce net_pnl, prorated by matched qty."""
    legs = compute_realized_pnl([
        trade(1, side="BUY",  qty=100, price=10.0, fees=1.00),
        trade(2, side="SELL", qty=100, price=12.0, fees=0.50, minute=5),
    ])
    assert legs[0].gross_pnl == pytest.approx(200.0)
    assert legs[0].fees == pytest.approx(1.50)
    assert legs[0].net_pnl == pytest.approx(198.50)


def test_fees_split_proportionally_on_partial_close() -> None:
    """BUY 100 with $1 fees, then close half -> only $0.50 should attach."""
    legs = compute_realized_pnl([
        trade(1, side="BUY",  qty=100, price=10.0, fees=1.00),
        trade(2, side="SELL", qty=50,  price=12.0, fees=0.0, minute=5),
    ])
    assert len(legs) == 1
    # 50% of the opening fees applied to this leg
    assert legs[0].fees == pytest.approx(0.50)
    assert legs[0].net_pnl == pytest.approx(100.0 - 0.50)


# ---------- Symbol isolation ----------


def test_symbols_are_independent() -> None:
    """A SELL of TSLA must NOT close an open AAPL lot."""
    legs = compute_realized_pnl([
        trade(1, side="BUY",  qty=10, price=100.0, symbol="AAPL"),
        trade(2, side="SELL", qty=10, price=200.0, symbol="TSLA", minute=5),
    ])
    # The SELL opens a SHORT on TSLA; nothing closed on AAPL
    assert legs == []


def test_accounts_are_independent() -> None:
    """A buy on account A and a sell on account B don't match."""
    legs = compute_realized_pnl([
        trade(1, side="BUY",  qty=10, price=100.0, account="A"),
        trade(2, side="SELL", qty=10, price=200.0, account="B", minute=5),
    ])
    assert legs == []


# ---------- Summaries ----------


def test_summarize_by_symbol_groups_winners_losers() -> None:
    legs = compute_realized_pnl([
        trade(1, side="BUY",  qty=10, price=10.0, symbol="A"),
        trade(2, side="SELL", qty=10, price=12.0, symbol="A", minute=5),  # +20
        trade(3, side="BUY",  qty=10, price=10.0, symbol="B", minute=10),
        trade(4, side="SELL", qty=10, price=8.0,  symbol="B", minute=15), # -20
    ])
    summary = summarize_by_symbol(legs)
    by_sym = {row["symbol"]: row for row in summary}
    assert by_sym["A"]["win_count"] == 1
    assert by_sym["A"]["loss_count"] == 0
    assert by_sym["A"]["net_pnl"] == pytest.approx(20.0)
    assert by_sym["B"]["win_count"] == 0
    assert by_sym["B"]["loss_count"] == 1
    assert by_sym["B"]["net_pnl"] == pytest.approx(-20.0)
    # Sort: winners first
    assert summary[0]["symbol"] == "A"


def test_summarize_by_day_sums_across_trades() -> None:
    legs = compute_realized_pnl([
        trade(1, side="BUY",  qty=1, price=10.0),
        trade(2, side="SELL", qty=1, price=12.0, minute=5),   # day 1: +2
        trade(3, side="BUY",  qty=1, price=10.0, minute=24*60),
        trade(4, side="SELL", qty=1, price=15.0, minute=24*60 + 5),  # day 2: +5
    ])
    days = summarize_by_day(legs)
    assert len(days) == 2
    assert days[0]["net_pnl"] == pytest.approx(2.0)
    assert days[1]["net_pnl"] == pytest.approx(5.0)


def test_overall_summary_win_rate() -> None:
    legs = compute_realized_pnl([
        trade(1, side="BUY",  qty=1, price=10.0),
        trade(2, side="SELL", qty=1, price=12.0, minute=5),
        trade(3, side="BUY",  qty=1, price=10.0, minute=10),
        trade(4, side="SELL", qty=1, price=8.0,  minute=15),
        trade(5, side="BUY",  qty=1, price=10.0, minute=20),
        trade(6, side="SELL", qty=1, price=15.0, minute=25),
    ])
    s = overall_summary(legs)
    assert s["closed_trade_count"] == 3
    assert s["win_count"] == 2
    assert s["loss_count"] == 1
    assert s["win_rate"] == pytest.approx(2/3, abs=1e-4)
    assert s["total_realized_pnl"] == pytest.approx(2 - 2 + 5)


def test_overall_summary_handles_empty_input() -> None:
    s = overall_summary([])
    assert s["closed_trade_count"] == 0
    assert s["win_rate"] == 0.0
    assert s["total_realized_pnl"] == 0.0
