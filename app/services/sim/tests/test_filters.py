"""Composable A+ filter tests (Milestone 2)."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import pytest

from app.services.sim.context import Context
from app.services.sim.filters import (
    FilteredSignalSource,
    MinRewardRiskFilter,
    TrendFilter,
    VolumeExpansionFilter,
    build_filter,
)
from app.services.sim.schemas import BacktestConfig, PortfolioSnapshot
from app.services.sim.signal_source import BaseSignalSource, Signal

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


def _cfg() -> BacktestConfig:
    return BacktestConfig(
        symbols=["TEST"], start=T0, end=dt.datetime(2024, 12, 31, tzinfo=UTC),
        interval="1d", starting_cash=40_000.0, history_window=200,
    )


def _flat() -> PortfolioSnapshot:
    return PortfolioSnapshot(cash=40_000.0, equity=40_000.0, positions={}, n_trades=0)


def _ctx_with_closes(closes, vols=None) -> Context:
    ctx = Context(config=_cfg())
    for i, c in enumerate(closes):
        v = vols[i] if vols else 1_000_000.0
        ctx.advance(
            _Bar("TEST", T0 + dt.timedelta(days=i), open=c, high=c, low=c, close=c, volume=v),
            _flat(),
        )
    return ctx


def _sig(entry=100.0, stop=95.0, target_1=110.0) -> Signal:
    return Signal("TEST", "long", entry=entry, stop=stop, target_1=target_1, kind="stub")


class _StubBase(BaseSignalSource):
    name = "stub"

    def __init__(self, signal):
        self._signal = signal

    def on_bar(self, ctx):
        return self._signal


# ── individual filters ──────────────────────────────────────────────


def test_trend_filter_passes_in_uptrend() -> None:
    ctx = _ctx_with_closes([float(x) for x in range(90, 110)])  # rising
    f = TrendFilter(ma="sma", period=5)
    res = f.evaluate(ctx, _sig(entry=109.0))
    assert res.passed and res.score == 1.0


def test_trend_filter_fails_below_ma() -> None:
    ctx = _ctx_with_closes([float(x) for x in range(120, 100, -1)])  # falling
    f = TrendFilter(ma="sma", period=5)
    res = f.evaluate(ctx, _sig(entry=101.0))
    assert not res.passed and res.score == 0.0


def test_reward_risk_filter() -> None:
    f = MinRewardRiskFilter(min_rr=1.5)
    ctx = _ctx_with_closes([100.0])
    assert f.evaluate(ctx, _sig(target_1=110.0)).passed       # rr=2.0
    assert not f.evaluate(ctx, _sig(target_1=101.0)).passed   # rr=0.2


def test_volume_filter() -> None:
    closes = [100.0] * 22
    vols = [1_000.0] * 21 + [3_000.0]   # last bar 3x average
    ctx = _ctx_with_closes(closes, vols)
    assert VolumeExpansionFilter(mult=1.5, period=20).evaluate(ctx, _sig()).passed
    vols2 = [1_000.0] * 22
    ctx2 = _ctx_with_closes(closes, vols2)
    assert not VolumeExpansionFilter(mult=1.5, period=20).evaluate(ctx2, _sig()).passed


# ── FilteredSignalSource composition ────────────────────────────────


def test_all_mode_blocks_when_one_fails() -> None:
    ctx = _ctx_with_closes([100.0])
    src = FilteredSignalSource(
        _StubBase(_sig(target_1=101.0)),           # rr=0.2
        [MinRewardRiskFilter(min_rr=1.5)],
        mode="all",
    )
    assert src.on_bar(ctx) is None


def test_all_mode_passes_and_sets_confidence() -> None:
    ctx = _ctx_with_closes([100.0])
    src = FilteredSignalSource(
        _StubBase(_sig(target_1=110.0)),           # rr=2.0
        [MinRewardRiskFilter(min_rr=1.5)],
        mode="all",
    )
    out = src.on_bar(ctx)
    assert out is not None
    assert out.confidence == 1.0
    assert "A+[reward_risk]" in out.rationale


def test_score_mode_threshold() -> None:
    ctx = _ctx_with_closes([100.0])
    # Two filters, one passes (rr) one fails (rr stricter). Score 1 of 2.
    src = FilteredSignalSource(
        _StubBase(_sig(target_1=110.0)),  # rr=2.0
        [MinRewardRiskFilter(min_rr=1.5, weight=1.0),
         MinRewardRiskFilter(min_rr=5.0, weight=1.0)],
        mode="score", min_score=1.0,
    )
    out = src.on_bar(ctx)
    assert out is not None and out.confidence == pytest.approx(0.5)
    # Raise the bar above the achievable score → blocked.
    src2 = FilteredSignalSource(
        _StubBase(_sig(target_1=110.0)),
        [MinRewardRiskFilter(min_rr=1.5), MinRewardRiskFilter(min_rr=5.0)],
        mode="score", min_score=2.0,
    )
    assert src2.on_bar(ctx) is None


def test_empty_filters_passthrough() -> None:
    ctx = _ctx_with_closes([100.0])
    src = FilteredSignalSource(_StubBase(_sig()), [], mode="all")
    assert src.on_bar(ctx) is not None


def test_build_unknown_filter_raises() -> None:
    with pytest.raises(ValueError, match="Unknown filter"):
        build_filter("nope")


# ── market-aware filters (regime / relative strength) ───────────────

def _market(values: list[float], n: int = 60):
    import pandas as pd
    from app.services.sim.market_context import MarketContext
    idx = pd.DatetimeIndex([T0 + dt.timedelta(days=i) for i in range(len(values))])
    return MarketContext("SPY", pd.Series(values, index=idx))


def test_regime_filter_passes_in_uptrend() -> None:
    from app.services.sim.filters import RegimeFilter
    ctx = _ctx_with_closes([100.0] * 10)  # ctx.bar at T0+9
    ctx.market = _market([100.0 + i for i in range(60)])  # rising benchmark
    assert RegimeFilter(ma_period=5).evaluate(ctx, _sig()).passed


def test_regime_filter_fails_in_downtrend() -> None:
    from app.services.sim.filters import RegimeFilter
    ctx = _ctx_with_closes([100.0] * 10)
    ctx.market = _market([160.0 - i for i in range(60)])  # falling benchmark
    assert not RegimeFilter(ma_period=5).evaluate(ctx, _sig()).passed


def test_regime_filter_fails_without_benchmark() -> None:
    from app.services.sim.filters import RegimeFilter
    ctx = _ctx_with_closes([100.0] * 10)  # ctx.market is None
    res = RegimeFilter(ma_period=5).evaluate(ctx, _sig())
    assert not res.passed and "no benchmark" in res.reason


def test_relative_strength_filter() -> None:
    from app.services.sim.filters import RelativeStrengthFilter
    ctx = _ctx_with_closes([100.0 + i * 2 for i in range(20)])  # symbol +strong
    ctx.market = _market([100.0] * 20)                          # benchmark flat
    assert RelativeStrengthFilter(lookback=10).evaluate(ctx, _sig()).passed
    ctx2 = _ctx_with_closes([100.0] * 20)                       # symbol flat
    ctx2.market = _market([100.0 + i * 2 for i in range(20)])   # benchmark +strong
    assert not RelativeStrengthFilter(lookback=10).evaluate(ctx2, _sig()).passed
