"""Short-selling P&L correctness — the engine must get short round-trips right."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.services.sim.fees import NextBarOpenFill, ZeroFees
from app.services.sim.portfolio import Portfolio
from app.services.sim.schemas import Action

UTC = dt.timezone.utc
T0 = dt.datetime(2024, 1, 1, tzinfo=UTC)


@dataclass
class _Bar:
    symbol: str
    timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 1_000_000.0


def _bar(i, o, c):
    t = T0 + dt.timedelta(days=i)
    return _Bar("X", t, open=o, high=max(o, c), low=min(o, c), close=c)


def test_short_round_trip_profit() -> None:
    pf = Portfolio(starting_cash=40_000.0)
    fees, slip = ZeroFees(), NextBarOpenFill()
    # Open short 10 @ next-open 100.
    pf.apply(Action(kind="sell", symbol="X", size=10), _bar(0, 100, 100), _bar(1, 100, 99), fees, slip)
    assert pf.cash == 41_000.0                       # +proceeds
    assert pf.positions["X"].quantity == -10
    pf.mark_to_market(_bar(1, 100, 95))
    assert pf.equity_curve[-1][1] == 40_050.0        # short 10 @100, mark 95 → +$50

    # Cover 10 @ next-open 90 → profit (100-90)*10 = $100.
    pf.apply(Action(kind="buy", symbol="X", size=10), _bar(2, 90, 90), _bar(3, 90, 90), fees, slip)
    assert "X" not in pf.positions
    close_legs = [t for t in pf.closed_trades if t.is_closing]
    assert len(close_legs) == 1
    assert close_legs[0].realized_pnl == 100.0
    pf.mark_to_market(_bar(3, 90, 90))
    assert round(pf.equity_curve[-1][1], 2) == 40_100.0   # start + $100


def test_short_round_trip_loss() -> None:
    pf = Portfolio(starting_cash=40_000.0)
    fees, slip = ZeroFees(), NextBarOpenFill()
    pf.apply(Action(kind="sell", symbol="X", size=10), _bar(0, 100, 100), _bar(1, 100, 100), fees, slip)
    pf.apply(Action(kind="buy", symbol="X", size=10), _bar(2, 110, 110), _bar(3, 110, 110), fees, slip)
    close_legs = [t for t in pf.closed_trades if t.is_closing]
    assert close_legs[0].realized_pnl == -100.0      # covered higher → loss
    pf.mark_to_market(_bar(3, 110, 110))
    assert round(pf.equity_curve[-1][1], 2) == 39_900.0
