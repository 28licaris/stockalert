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


def _armed_strategy(policy: str, expiry: int = 5) -> AlertStrategy:
    """Strategy with a working order armed: signal entry 105, level/stop 100,
    target 115 (risk 5, rr 2)."""
    sig = Signal("TEST", "long", entry=105.0, stop=100.0, target_1=115.0,
                 confidence=0.5, kind="stub")
    strat = AlertStrategy(AlertStrategyParams(entry_policy=policy,
                                              entry_expiry_days=expiry))
    strat.source = _StubSource(sig)
    strat._pending["TEST"] = [sig, expiry, None, False, None]
    return strat


def _work_day(strat, intraday, *, day=6, close=104.0, high=None, low=None) -> Action:
    ctx = Context(config=_cfg(), intervals=["1d"])
    ctx.intraday = intraday
    bar = _bar(day, close, high=high, low=low)
    ctx.advance(bar, _flat())
    return strat.on_bar(ctx)


def test_retest_limit_fills_at_level_and_reanchors_plan():
    day = (T0 + dt.timedelta(days=6)).date()
    path = _FakePath({day: [
        _hour(T0 + dt.timedelta(days=6), 0, 104, 104.5, 101, 102),   # no touch
        _hour(T0 + dt.timedelta(days=6), 1, 102, 102.5, 99.8, 101),  # touches 100
    ]})
    strat = _armed_strategy("retest_limit")
    a = _work_day(strat, path, close=104, high=105, low=99.8)
    assert a.kind == "buy" and a.fill_at_level == 100.0
    plan = strat._plans["TEST"]
    assert plan.stop == 95.0 and plan.target_1 == 110.0  # risk 5, rr 2 re-anchored
    assert "TEST" not in strat._pending


def test_retest_limit_gap_below_fills_at_better_open():
    day = (T0 + dt.timedelta(days=6)).date()
    path = _FakePath({day: [
        _hour(T0 + dt.timedelta(days=6), 0, 98.0, 99.5, 97.5, 99.0),
    ]})
    a = _work_day(_armed_strategy("retest_limit"), path, close=99, high=99.5, low=97.5)
    assert a.kind == "buy" and a.fill_at_level == 98.0  # limit fills at the open


def test_working_order_expires_after_n_bars():
    strat = _armed_strategy("retest_limit", expiry=2)
    quiet = _FakePath({})  # no touches, no data → just age the order
    for day in (6, 7):
        a = _work_day(strat, quiet, day=day, close=104, high=106, low=103)
        assert a.kind == "hold"
    assert "TEST" not in strat._pending  # expired


def test_working_order_cancelled_on_close_below_level():
    strat = _armed_strategy("retest_limit")
    a = _work_day(strat, _FakePath({}), close=99.0, high=106, low=98.5)
    assert a.kind == "hold" and "TEST" not in strat._pending


def test_hourly_pullback_enters_on_turn_up_with_structure_stop():
    day = (T0 + dt.timedelta(days=6)).date()
    d6 = T0 + dt.timedelta(days=6)
    path = _FakePath({day: [
        _hour(d6, 0, 105, 106, 103, 103.5),   # pullback begins (low < 105)
        _hour(d6, 1, 103.5, 104, 102, 103),   # pull_low → 102
        _hour(d6, 2, 103, 105, 103, 104.5),   # close 104.5 > prev high 104 → turn-up
    ]})
    strat = _armed_strategy("hourly_pullback")
    a = _work_day(strat, path, close=104.5, high=106, low=102)
    assert a.kind == "buy" and a.fill_at_level == 104.5
    plan = strat._plans["TEST"]
    assert plan.stop == 102.0                       # hourly structure low
    assert abs(plan.target_1 - (104.5 + 2 * 2.5)) < 1e-9


def test_eod_stop_ignores_wick_but_exits_on_close_through():
    day5 = T0 + dt.timedelta(days=5)
    wick_day = _FakePath({day5.date(): [
        _hour(day5, 0, 100, 101, 94.0, 100.5),   # wick through 95, recovers
    ]})
    sig = Signal("TEST", "long", entry=100.0, stop=95.0, target_1=110.0,
                 confidence=0.5, kind="stub")
    strat = AlertStrategy(AlertStrategyParams(stop_trigger="close"))
    strat.source = _StubSource(sig)
    strat._plans["TEST"] = sig
    ctx = Context(config=_cfg(), intervals=["1d"])
    ctx.intraday = wick_day
    ctx.advance(_bar(5, 100.5, high=101, low=94), _with_pos(80, 100.0))
    assert strat.on_bar(ctx).kind == "hold"        # wick didn't take us out
    # next day CLOSES through the stop → exit, next-open fill (no level fill)
    day6 = T0 + dt.timedelta(days=6)
    ctx2 = Context(config=_cfg(), intervals=["1d"])
    ctx2.intraday = _FakePath({day6.date(): [_hour(day6, 0, 96, 96.5, 93, 93.5)]})
    ctx2.advance(_bar(6, 93.5, high=96.5, low=93), _with_pos(80, 100.0))
    a = strat.on_bar(ctx2)
    assert a.kind == "sell" and a.fill_at_level is None and "stop(close)" in a.note


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
