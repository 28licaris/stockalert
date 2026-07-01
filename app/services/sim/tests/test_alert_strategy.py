"""
AlertStrategy + SignalSource bridge tests (Milestone 1).

Drives the strategy through a real `Context` (no ClickHouse): verifies
risk-based entry sizing, the reward:risk gate, stop/target exits, and that
the two computed-on-the-fly sources (ma_cross, breakout) fire on the right bar.
This is the contract that makes alerts backtestable.
"""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest

from app.services.sim.context import Context
from app.services.sim.schemas import BacktestConfig, PortfolioSnapshot, Position
from app.services.sim.signal_source import (
    BaseSignalSource,
    BreakoutSignalSource,
    MACrossSignalSource,
    Signal,
)
from app.services.sim.strategies.alert_strategy import AlertStrategy, AlertStrategyParams

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


def _cfg(history_window: int = 200) -> BacktestConfig:
    return BacktestConfig(
        symbols=["TEST"], start=T0, end=dt.datetime(2024, 12, 31, tzinfo=UTC),
        interval="1d", starting_cash=40_000.0, history_window=history_window,
    )


def _bar(i: int, close: float, *, high=None, low=None, vol=1_000_000.0) -> _Bar:
    return _Bar(
        "TEST", T0 + dt.timedelta(days=i),
        open=close, high=close if high is None else high,
        low=close if low is None else low, close=close, volume=vol,
    )


def _flat(equity: float = 40_000.0, cash: float = 40_000.0) -> PortfolioSnapshot:
    return PortfolioSnapshot(cash=cash, equity=equity, positions={}, n_trades=0)


def _with_pos(qty: float, entry: float) -> PortfolioSnapshot:
    pos = Position(symbol="TEST", quantity=qty, avg_entry_price=entry, entry_time=T0)
    return PortfolioSnapshot(cash=0.0, equity=qty * entry, positions={"TEST": pos}, n_trades=1)


class _StubSource(BaseSignalSource):
    """Emits a fixed signal once, then nothing."""

    name = "stub"

    def __init__(self, signal: Signal) -> None:
        self._signal = signal
        self._fired = False

    def on_bar(self, ctx):
        if self._fired:
            return None
        self._fired = True
        return self._signal


# ─────────────────────────────────────────────────────────────────────
# Executor: sizing, gates, exits
# ─────────────────────────────────────────────────────────────────────


def test_entry_risk_based_sizing() -> None:
    # entry 100, stop 95 → $5/share risk. 1% of $40k equity = $400 → 80 shares.
    sig = Signal("TEST", "long", entry=100.0, stop=95.0, target_1=110.0, confidence=0.5, kind="stub")
    strat = AlertStrategy(AlertStrategyParams(risk_pct=0.01))
    strat.source = _StubSource(sig)
    ctx = Context(config=_cfg())
    strat.setup(ctx)
    ctx.advance(_bar(0, 100.0), _flat())
    action = strat.on_bar(ctx)
    assert action.kind == "buy"
    assert action.size == 80  # min(risk-cap 80, cash-cap 380)
    assert "TEST" in strat._plans


def test_entry_capped_by_cash() -> None:
    # Tiny stop distance → risk sizing wants a huge qty; cash cap binds.
    sig = Signal("TEST", "long", entry=100.0, stop=99.9, target_1=101.0, confidence=0.5, kind="stub")
    strat = AlertStrategy(AlertStrategyParams(risk_pct=0.01, max_cash_pct=0.95, min_reward_risk=0.0))
    strat.source = _StubSource(sig)
    ctx = Context(config=_cfg())
    strat.setup(ctx)
    ctx.advance(_bar(0, 100.0), _flat())
    action = strat.on_bar(ctx)
    assert action.kind == "buy"
    assert action.size == 380  # floor(40000*0.95 / 100)


def test_skips_below_reward_risk_gate() -> None:
    sig = Signal("TEST", "long", entry=100.0, stop=95.0, target_1=101.0, kind="stub")  # rr=0.2
    strat = AlertStrategy(AlertStrategyParams(min_reward_risk=1.5))
    strat.source = _StubSource(sig)
    ctx = Context(config=_cfg())
    strat.setup(ctx)
    ctx.advance(_bar(0, 100.0), _flat())
    assert strat.on_bar(ctx).kind == "hold"


def test_short_signal_opens_short() -> None:
    # short: entry 100, stop 105 (above), target 90 (below) → rr=10/5=2.0. Opens via SELL.
    sig = Signal("TEST", "short", entry=100.0, stop=105.0, target_1=90.0, confidence=0.5, kind="stub")
    strat = AlertStrategy(AlertStrategyParams(risk_pct=0.01, min_reward_risk=1.5))
    strat.source = _StubSource(sig)
    ctx = Context(config=_cfg())
    strat.setup(ctx)
    ctx.advance(_bar(0, 100.0), _flat())
    action = strat.on_bar(ctx)
    assert action.kind == "sell" and action.size > 0  # sell-to-open a short
    assert strat._plans["TEST"].direction == "short"


def test_exit_on_stop() -> None:
    strat = AlertStrategy()
    ctx = Context(config=_cfg())
    strat.setup(ctx)
    strat._plans["TEST"] = Signal("TEST", "long", entry=100.0, stop=95.0, target_1=110.0, kind="stub")
    ctx.advance(_bar(1, 96.0, high=97.0, low=94.0), _with_pos(80, 100.0))  # low pierces stop
    action = strat.on_bar(ctx)
    assert action.kind == "sell" and action.size == 80
    assert "TEST" not in strat._plans


def test_exit_on_target() -> None:
    strat = AlertStrategy()
    ctx = Context(config=_cfg())
    strat.setup(ctx)
    strat._plans["TEST"] = Signal("TEST", "long", entry=100.0, stop=95.0, target_1=110.0, kind="stub")
    ctx.advance(_bar(1, 109.0, high=111.0, low=108.0), _with_pos(80, 100.0))  # high pierces target
    action = strat.on_bar(ctx)
    assert action.kind == "sell" and action.size == 80
    assert "TEST" not in strat._plans


def test_holds_inside_band() -> None:
    strat = AlertStrategy()
    ctx = Context(config=_cfg())
    strat.setup(ctx)
    strat._plans["TEST"] = Signal("TEST", "long", entry=100.0, stop=95.0, target_1=110.0, kind="stub")
    ctx.advance(_bar(1, 102.0, high=103.0, low=99.0), _with_pos(80, 100.0))  # neither touched
    assert strat.on_bar(ctx).kind == "hold"


# ─────────────────────────────────────────────────────────────────────
# Sources fire on the right bar (computed from Context, no look-ahead)
# ─────────────────────────────────────────────────────────────────────


def test_macross_source_emits_long_on_cross() -> None:
    src = MACrossSignalSource(fast_period=3, slow_period=5, ma="ema", stop_pct=0.05)
    ctx = Context(config=_cfg())
    closes = [100, 98, 96, 94, 92, 90, 88, 100, 112, 125]  # downtrend then spike → fast crosses up
    got = None
    for i, c in enumerate(closes):
        ctx.advance(_bar(i, float(c)), _flat())
        s = src.on_bar(ctx)
        if s is not None:
            got = s
            break
    assert got is not None
    assert got.kind == "ma_cross" and got.direction == "long"
    assert got.stop < got.entry < got.target_1


def test_breakout_source_emits_on_new_high_with_volume() -> None:
    src = BreakoutSignalSource(lookback=5, vol_mult=1.5, vol_avg_period=5)
    ctx = Context(config=_cfg())
    bars = [(100.0, 1000.0)] * 6 + [(105.0, 3000.0)]  # break prior 100-high on 3x volume
    got = None
    for i, (c, v) in enumerate(bars):
        ctx.advance(_bar(i, c, vol=v), _flat())
        s = src.on_bar(ctx)
        if s is not None:
            got = s
    assert got is not None
    assert got.kind == "breakout"
    assert got.entry == 105.0
    assert got.stop < 105.0 < got.target_1


def test_breakout_skipped_without_volume_expansion() -> None:
    src = BreakoutSignalSource(lookback=5, vol_mult=1.5, vol_avg_period=5)
    ctx = Context(config=_cfg())
    bars = [(100.0, 1000.0)] * 6 + [(105.0, 1000.0)]  # new high but flat volume
    got = None
    for i, (c, v) in enumerate(bars):
        ctx.advance(_bar(i, c, vol=v), _flat())
        got = src.on_bar(ctx) or got
    assert got is None


def test_build_unknown_source_raises() -> None:
    from app.services.sim.signal_source import build_signal_source
    with pytest.raises(ValueError, match="Unknown signal source"):
        build_signal_source("does_not_exist")


def test_divergence_source_registered_and_warmup_safe() -> None:
    from app.services.sim.signal_source import build_signal_source, list_signal_sources
    assert "divergence" in list_signal_sources()
    src = build_signal_source("divergence", lookback=60, pivot_k=3)
    ctx = Context(config=_cfg())
    # Far fewer bars than the source needs → must return None, never raise.
    for i in range(10):
        ctx.advance(_bar(i, 100.0 + i), _flat())
        assert src.on_bar(ctx) is None


def test_time_stop_exits_after_max_holding_days() -> None:
    strat = AlertStrategy(AlertStrategyParams(max_holding_days=5))
    ctx = Context(config=_cfg())
    strat.setup(ctx)
    strat._plans["TEST"] = Signal("TEST", "long", entry=100.0, stop=90.0, target_1=130.0, kind="stub")
    # 7 days held, neither stop (90) nor target (130) touched → time stop fires.
    bar = _Bar("TEST", T0 + dt.timedelta(days=7), open=101.0, high=102.0, low=99.0, close=101.0)
    pos = Position(symbol="TEST", quantity=50, avg_entry_price=100.0, entry_time=T0)
    ctx.advance(bar, PortfolioSnapshot(cash=0.0, equity=5050.0, positions={"TEST": pos}, n_trades=1))
    action = strat.on_bar(ctx)
    assert action.kind == "sell" and "time stop" in action.note


def test_conviction_scaled_sizing() -> None:
    # risk scales 1%→5% by confidence. conf=0.5 → 3% of $40k = $1200 / $5 risk = 240 sh.
    sig = Signal("TEST", "long", entry=100.0, stop=95.0, target_1=120.0, confidence=0.5, kind="stub")
    strat = AlertStrategy(AlertStrategyParams(risk_pct=0.01, max_risk_pct=0.05, min_reward_risk=0.0))
    strat.source = _StubSource(sig)
    ctx = Context(config=_cfg())
    strat.setup(ctx)
    ctx.advance(_bar(0, 100.0), _flat())
    assert strat.on_bar(ctx).size == 240
