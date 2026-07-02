"""
Path-aware intra-bar fills (Stage 1 of the intraday program).

With ctx.intraday present, AlertStrategy exits must be ordered by FIRST
intraday touch and fill AT the level on the current bar (gap-through days
fill at the open); without it, behavior is byte-identical to the legacy
whole-bar worst-case + next-open model. Portfolio.apply must honor
Action.fill_at_level (price == level, timestamp == current bar).
"""
from __future__ import annotations

import datetime as dt

from app.services.sim.context import Context
from app.services.sim.fees import NextBarOpenFill, ZeroFees
from app.services.sim.intraday import HourBar
from app.services.sim.portfolio import Portfolio
from app.services.sim.schemas import Action, BacktestConfig, PortfolioSnapshot, Position
from app.services.sim.signal_source import Signal
from app.services.sim.strategies.alert_strategy import AlertStrategy, AlertStrategyParams
from app.services.sim.tests.test_alert_strategy import (
    _bar,
    _flat,
    _StubSource,
    _with_pos,
    T0,
)

UTC = dt.timezone.utc


class _FakePath:
    """ctx.intraday stand-in: day → hourly bars for the TEST symbol."""

    def __init__(self, by_day):
        self._by_day = by_day

    def bars_for(self, symbol, day):
        return self._by_day.get(day, [])


def _hour(day_ts: dt.datetime, i: int, o, h, lo, c) -> HourBar:
    return HourBar(day_ts.replace(hour=14, minute=30) + dt.timedelta(hours=i),
                   o, h, lo, c, 1e5)


def _cfg() -> BacktestConfig:
    return BacktestConfig(symbols=["TEST"], start=T0,
                          end=dt.datetime(2024, 12, 31, tzinfo=UTC),
                          interval="1d", starting_cash=40_000.0, history_window=50)


def _entered_strategy() -> AlertStrategy:
    """Strategy holding a long plan: entry 100, stop 95, target 110.
    (Exits never consult the source, so the built-in default is fine.)"""
    sig = Signal("TEST", "long", entry=100.0, stop=95.0, target_1=110.0,
                 confidence=0.5, kind="stub")
    strat = AlertStrategy(AlertStrategyParams())
    strat.source = _StubSource(sig)
    strat._plans["TEST"] = sig
    return strat


def _exit_action(strat, intraday, *, high, low) -> Action:
    """Advance one daily bar (holding 80 shares) and return the exit action."""
    ctx = Context(config=_cfg(), intervals=["1d"])
    ctx.intraday = intraday
    bar = _bar(5, 100.0, high=high, low=low)
    ctx.advance(bar, _with_pos(80, 100.0))
    return strat.on_bar(ctx)


def test_target_first_touch_beats_worst_case():
    # Daily bar spans BOTH levels; the hourly path hits the TARGET first.
    # Legacy worst-case would call this a stop-out; the path says winner.
    day = (T0 + dt.timedelta(days=5)).date()
    path = _FakePath({day: [
        _hour(T0 + dt.timedelta(days=5), 0, 100, 110.5, 99, 110),   # target touched
        _hour(T0 + dt.timedelta(days=5), 1, 110, 110, 94, 94.5),    # stop later
    ]})
    a = _exit_action(_entered_strategy(), path, high=111, low=94)
    assert a.kind == "sell" and a.fill_at_level == 110.0


def test_stop_first_touch_fills_at_stop_level():
    day = (T0 + dt.timedelta(days=5)).date()
    path = _FakePath({day: [
        _hour(T0 + dt.timedelta(days=5), 0, 100, 101, 94.8, 95.2),  # stop touched
        _hour(T0 + dt.timedelta(days=5), 1, 95, 111, 95, 110.5),    # target later
    ]})
    a = _exit_action(_entered_strategy(), path, high=111, low=94)
    assert a.kind == "sell" and a.fill_at_level == 95.0


def test_gap_through_stop_fills_at_open():
    # First hour OPENS below the stop → you get the (worse) open, not the level.
    day = (T0 + dt.timedelta(days=5)).date()
    path = _FakePath({day: [
        _hour(T0 + dt.timedelta(days=5), 0, 93.0, 94.0, 92.0, 93.5),
    ]})
    a = _exit_action(_entered_strategy(), path, high=94, low=92)
    assert a.kind == "sell" and a.fill_at_level == 93.0


def test_no_path_falls_back_to_legacy():
    a = _exit_action(_entered_strategy(), _FakePath({}), high=111, low=94)
    # Legacy worst-case: stop wins on a both-touched bar; no level fill —
    # the harness fills at next open exactly as before.
    assert a.kind == "sell" and a.fill_at_level is None and "stop" in a.note


def test_portfolio_fills_at_level_on_current_bar():
    p = Portfolio(starting_cash=1.0)
    p.positions["TEST"] = Position(symbol="TEST", quantity=80,
                                   avg_entry_price=100.0, entry_time=T0)
    cur = _bar(5, 100.0, high=111, low=94)
    nxt = _bar(6, 90.0)  # next open would be 90 — must NOT be used
    trade = p.apply(Action(kind="sell", symbol="TEST", size=80, fill_at_level=110.0),
                    cur, nxt, ZeroFees(), NextBarOpenFill())
    assert trade is not None
    assert trade.price == 110.0
    assert trade.timestamp == cur.timestamp  # current bar, not next
