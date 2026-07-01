"""BreakdownSignalSource (short mirror) + CompositeSignalSource tests."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.services.sim.context import Context
from app.services.sim.schemas import BacktestConfig, PortfolioSnapshot
from app.services.sim.signal_source import build_signal_source

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


def _cfg():
    return BacktestConfig(symbols=["X"], start=T0, end=dt.datetime(2024, 12, 31, tzinfo=UTC),
                          interval="1d", starting_cash=40_000.0, history_window=200)


def _ctx(closes, vols):
    ctx = Context(config=_cfg())
    for i, (c, v) in enumerate(zip(closes, vols)):
        ctx.advance(_Bar("X", T0 + dt.timedelta(days=i), c, c + 0.5, c - 0.5, c, v),
                    PortfolioSnapshot(cash=40_000.0, equity=40_000.0, positions={}, n_trades=0))
    return ctx


def test_breakdown_shorts_new_low_on_volume():
    closes = [100.0] * 29 + [95.0]          # last breaks below the prior 20-bar low
    vols = [1_000_000.0] * 29 + [2_000_000.0]
    sig = build_signal_source("breakdown", lookback=20, reward_risk_mult=2.0).on_bar(_ctx(closes, vols))
    assert sig is not None and sig.direction == "short"
    assert sig.stop > sig.entry and sig.target_1 < sig.entry     # short geometry
    assert sig.kind == "breakdown"


def test_breakdown_silent_on_uptrend():
    closes = [100.0 + i for i in range(30)]
    vols = [2_000_000.0] * 30
    assert build_signal_source("breakdown").on_bar(_ctx(closes, vols)) is None


def test_composite_emits_long_breakout():
    closes = [100.0] * 29 + [105.0]         # new high → breakout long
    vols = [1_000_000.0] * 29 + [2_000_000.0]
    src = build_signal_source("composite", sources=[
        {"name": "breakout", "params": {"lookback": 20}},
        {"name": "breakdown", "params": {"lookback": 20}},
    ])
    sig = src.on_bar(_ctx(closes, vols))
    assert sig is not None and sig.direction == "long" and sig.kind == "breakout"


def test_composite_emits_short_breakdown():
    closes = [100.0] * 29 + [95.0]          # new low → breakdown short
    vols = [1_000_000.0] * 29 + [2_000_000.0]
    src = build_signal_source("composite", sources=[
        {"name": "breakout", "params": {"lookback": 20}},
        {"name": "breakdown", "params": {"lookback": 20}},
    ])
    sig = src.on_bar(_ctx(closes, vols))
    assert sig is not None and sig.direction == "short" and sig.kind == "breakdown"


def test_registered():
    assert build_signal_source("breakdown") is not None
    assert build_signal_source("composite", sources=[{"name": "breakout"}]) is not None
