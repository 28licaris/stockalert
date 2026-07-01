"""Pro confluence filter tests: adx, atr_volatility, htf_trend, not_extended, rel_volume."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

from app.services.sim.context import Context
from app.services.sim.filters import (
    AdxStrengthFilter, AtrVolatilityFilter, HtfTrendFilter,
    NotExtendedFilter, RelativeVolumeFilter, build_filter,
)
from app.services.sim.schemas import BacktestConfig, PortfolioSnapshot
from app.services.sim.signal_source import Signal

UTC = dt.timezone.utc
T0 = dt.datetime(2023, 1, 2, tzinfo=UTC)  # a Monday


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
    return BacktestConfig(symbols=["TEST"], start=T0, end=dt.datetime(2024, 12, 31, tzinfo=UTC),
                          interval="1d", starting_cash=40_000.0, history_window=400)


def _flat():
    return PortfolioSnapshot(cash=40_000.0, equity=40_000.0, positions={}, n_trades=0)


def _ctx(n=120, start=100.0, step=1.0, rng=0.02, vols=None):
    """Rising daily series with a real H/L range."""
    ctx = Context(config=_cfg())
    c = start
    for i in range(n):
        v = vols[i] if vols else 1_000_000.0
        ctx.advance(_Bar("TEST", T0 + dt.timedelta(days=i), open=c, high=c * (1 + rng),
                         low=c * (1 - rng), close=c, volume=v), _flat())
        c += step
    return ctx


def _sig(direction="long", entry=None):
    e = entry if entry is not None else 100.0
    return Signal("TEST", direction, entry=e, stop=e * 0.95, target_1=e * 1.1, kind="stub")


def test_adx_filter_passes_strong_trend_fails_chop():
    f = AdxStrengthFilter(threshold=20.0)
    assert f.evaluate(_ctx(n=120, step=1.0), _sig()).passed          # strong uptrend
    # Chop: tiny alternating steps → weak ADX.
    ctx = Context(config=_cfg())
    c = 100.0
    for i in range(120):
        c2 = c + (1.0 if i % 2 else -1.0)
        ctx.advance(_Bar("TEST", T0 + dt.timedelta(days=i), c, max(c, c2) * 1.005,
                         min(c, c2) * 0.995, c2, 1_000_000.0), _flat())
        c = c2
    assert not f.evaluate(ctx, _sig()).passed


def test_atr_volatility_band():
    f = AtrVolatilityFilter(min_pct=0.01, max_pct=0.08)
    assert f.evaluate(_ctx(rng=0.02), _sig()).passed                 # ~4% ATR → in band
    assert not f.evaluate(_ctx(rng=0.0005), _sig()).passed           # dead → below band


def test_htf_trend_alignment_direction_aware():
    f = HtfTrendFilter(weeks=8)
    ctx = _ctx(n=120, step=1.0)        # ~24 weeks of uptrend, price above weekly MA
    assert f.evaluate(ctx, _sig("long")).passed
    assert not f.evaluate(ctx, _sig("short")).passed   # short fights the weekly uptrend


def test_not_extended_distance_gate():
    ctx = _ctx(n=120, step=1.0, rng=0.02)
    assert NotExtendedFilter(period=20, max_atr=10.0).evaluate(ctx, _sig()).passed   # generous
    assert not NotExtendedFilter(period=20, max_atr=0.5).evaluate(ctx, _sig()).passed  # too far


def test_rel_volume_participation():
    vols = [1_000_000.0] * 119 + [3_000_000.0]   # last bar 3× → rvol high
    assert RelativeVolumeFilter(period=50, mult=1.2).evaluate(_ctx(n=120, vols=vols), _sig()).passed
    flat = [1_000_000.0] * 120
    assert not RelativeVolumeFilter(period=50, mult=1.2).evaluate(_ctx(n=120, vols=flat), _sig()).passed


def test_registered_in_factory():
    for name in ("adx", "atr_volatility", "htf_trend", "not_extended", "rel_volume"):
        assert build_filter(name) is not None
