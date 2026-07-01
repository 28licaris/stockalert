"""RegimeSwitchStrategy routing: up-branch in up-regime, down-branch/cash in down."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.services.sim.context import Context
from app.services.sim.schemas import BacktestConfig, PortfolioSnapshot
from app.services.sim.signal_source import Signal
from app.services.sim.strategies.regime_switch import (
    RegimeBranch, RegimeSwitchParams, RegimeSwitchStrategy,
)

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


class _Mkt:
    benchmark = "SPY"

    def __init__(self, up):
        self._up = up

    def above_ma_asof(self, ts, period):
        return self._up


def _cfg():
    return BacktestConfig(symbols=["X"], start=T0, end=dt.datetime(2024, 12, 31, tzinfo=UTC),
                          interval="1d", starting_cash=40_000.0, history_window=200)


def _ctx(up=True):
    ctx = Context(config=_cfg())
    ctx.advance(_Bar("X", T0, 100, 101, 99, 100), PortfolioSnapshot(
        cash=40_000.0, equity=40_000.0, positions={}, n_trades=0))
    ctx.market = _Mkt(up)
    return ctx


def _long(): return Signal("X", "long", entry=100.0, stop=95.0, target_1=115.0, kind="stub")
def _short(): return Signal("X", "short", entry=100.0, stop=105.0, target_1=85.0, kind="stub")


def _strat(down=None):
    return RegimeSwitchStrategy(RegimeSwitchParams(up=RegimeBranch(source="breakout"), down=down))


def test_up_regime_enters_via_up_branch():
    s = _strat()
    ctx = _ctx(up=True)
    s.setup(ctx)
    s.up_source.on_bar = lambda c: _long()
    assert s.on_bar(ctx).kind == "buy"


def test_down_regime_cash_takes_no_entry():
    s = _strat(down=None)
    ctx = _ctx(up=False)
    s.setup(ctx)
    s.up_source.on_bar = lambda c: _long()       # up branch would fire...
    assert s.on_bar(ctx).kind == "hold"          # ...but we're in cash (down=None)


def test_down_regime_routes_to_down_branch():
    s = _strat(down=RegimeBranch(source="breakout"))
    ctx = _ctx(up=False)
    s.setup(ctx)
    s.up_source.on_bar = lambda c: _long()
    s.down_source.on_bar = lambda c: _short()
    assert s.on_bar(ctx).kind == "sell"          # short entry from the down branch
