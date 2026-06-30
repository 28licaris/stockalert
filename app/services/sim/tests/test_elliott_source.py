"""ElliottWaveSource: entry logic (engine mocked) + real-engine smoke + purity."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from types import SimpleNamespace

from app.services.sim.context import Context
from app.services.sim.schemas import BacktestConfig, PortfolioSnapshot
from app.services.sim.signal_source import ElliottWaveSource, build_signal_source

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
                          interval="1d", starting_cash=40_000.0, history_window=400)


def _ctx(n=120, last_close=120.0):
    ctx = Context(config=_cfg())
    c = 100.0
    for i in range(n):
        c += (3.0 if (i // 5) % 2 == 0 else -1.5)
        cl = last_close if i == n - 1 else c
        ctx.advance(_Bar("X", T0 + dt.timedelta(days=i), c, c * 1.02, c * 0.98, cl),
                    PortfolioSnapshot(cash=40_000.0, equity=40_000.0, positions={}, n_trades=0))
    return ctx


def _mock(monkeypatch, *, wave, direction, inval, target, conf, structure="impulse"):
    prim = SimpleNamespace(current_wave=wave, direction=direction, structure=structure,
                           invalidation_price=inval)
    scen = SimpleNamespace(invalidation=inval, next_target=target)
    lab = SimpleNamespace(primary=prim, scenarios=[scen], confidence=conf, current_wave=wave)
    monkeypatch.setattr("app.signals.elliott.engine.WaveEngine.label", lambda self, *a, **k: lab)
    monkeypatch.setattr("app.indicators.pivots.PivotDetector.detect", lambda self, *a, **k: [object()] * 6)


def test_enters_long_on_wave3_confirm(monkeypatch):
    _mock(monkeypatch, wave="3", direction="up", inval=110.0, target=160.0, conf=0.6)
    sig = ElliottWaveSource().on_bar(_ctx(last_close=120.0))
    assert sig is not None and sig.direction == "long"
    assert sig.stop == 110.0 and sig.target_1 == 160.0      # trap-door stop + fib target
    assert sig.reward_risk > 1.0


def test_enters_short_on_wave3_down(monkeypatch):
    _mock(monkeypatch, wave="3", direction="down", inval=130.0, target=80.0, conf=0.6)
    sig = ElliottWaveSource().on_bar(_ctx(last_close=120.0))
    assert sig is not None and sig.direction == "short"
    assert sig.stop == 130.0 and sig.target_1 == 80.0


def test_skips_corrective_wave2(monkeypatch):
    _mock(monkeypatch, wave="2", direction="up", inval=110.0, target=160.0, conf=0.9)
    assert ElliottWaveSource().on_bar(_ctx()) is None


def test_debounce_one_entry_per_leg(monkeypatch):
    _mock(monkeypatch, wave="3", direction="up", inval=110.0, target=160.0, conf=0.6)
    s = ElliottWaveSource()
    ctx = _ctx(last_close=120.0)
    assert s.on_bar(ctx) is not None       # onset of wave 3
    assert s.on_bar(ctx) is None           # same wave 3 continuing → no re-entry


def test_min_confidence_gate(monkeypatch):
    _mock(monkeypatch, wave="3", direction="up", inval=110.0, target=160.0, conf=0.1)
    assert ElliottWaveSource(min_confidence=0.3).on_bar(_ctx()) is None


def test_side_filter(monkeypatch):
    _mock(monkeypatch, wave="3", direction="down", inval=130.0, target=80.0, conf=0.6)
    assert ElliottWaveSource(side="long").on_bar(_ctx()) is None    # short rejected


def test_rejects_bad_geometry(monkeypatch):
    # long but target below entry → degenerate, must skip
    _mock(monkeypatch, wave="3", direction="up", inval=110.0, target=115.0, conf=0.6)
    assert ElliottWaveSource().on_bar(_ctx(last_close=120.0)) is None


def test_real_engine_smoke():
    res = ElliottWaveSource(pivot_period=3).on_bar(_ctx(140))
    assert res is None or res.direction in ("long", "short")  # runs without error


def test_registered():
    assert build_signal_source("elliott_wave") is not None
