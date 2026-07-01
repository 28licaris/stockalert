"""EWT impulse gate: decision logic (engine mocked) + real-engine smoke + purity."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from types import SimpleNamespace

from app.services.sim.context import Context
from app.services.sim.filters import EwtImpulseFilter, build_filter
from app.services.sim.schemas import BacktestConfig, PortfolioSnapshot
from app.services.sim.signal_source import Signal

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


def _ctx(n=120):
    ctx = Context(config=_cfg())
    c = 100.0
    for i in range(n):
        # zigzag-ish rise so pivots exist
        c += (3.0 if (i // 5) % 2 == 0 else -1.5)
        ctx.advance(_Bar("X", T0 + dt.timedelta(days=i), c, c * 1.02, c * 0.98, c),
                    PortfolioSnapshot(cash=40_000.0, equity=40_000.0, positions={}, n_trades=0))
    return ctx


def _sig(direction="long"):
    e = 100.0
    return Signal("X", direction, entry=e, stop=e * 0.95, target_1=e * 1.1, kind="stub")


def _mock_label(monkeypatch, *, wave, direction, conf):
    fake = SimpleNamespace(current_wave=wave, confidence=conf,
                           primary=SimpleNamespace(direction=direction))
    monkeypatch.setattr("app.signals.elliott.engine.WaveEngine.label",
                        lambda self, *a, **k: fake)
    # ensure the pivot pre-check passes regardless of synthetic data
    monkeypatch.setattr("app.indicators.pivots.PivotDetector.detect",
                        lambda self, *a, **k: [object()] * 6)


def test_passes_impulse_wave3_long(monkeypatch):
    _mock_label(monkeypatch, wave="3", direction="up", conf=0.6)
    assert EwtImpulseFilter(min_confidence=0.3).evaluate(_ctx(), _sig("long")).passed


def test_rejects_direction_mismatch(monkeypatch):
    _mock_label(monkeypatch, wave="3", direction="up", conf=0.6)
    assert not EwtImpulseFilter().evaluate(_ctx(), _sig("short")).passed


def test_rejects_corrective_wave(monkeypatch):
    _mock_label(monkeypatch, wave="2", direction="up", conf=0.9)
    assert not EwtImpulseFilter().evaluate(_ctx(), _sig("long")).passed


def test_rejects_low_confidence(monkeypatch):
    _mock_label(monkeypatch, wave="3", direction="up", conf=0.1)
    assert not EwtImpulseFilter(min_confidence=0.3).evaluate(_ctx(), _sig("long")).passed


def test_warmup_fails_closed():
    short = Context(config=_cfg())
    short.advance(_Bar("X", T0, 100, 101, 99, 100),
                  PortfolioSnapshot(cash=40_000.0, equity=40_000.0, positions={}, n_trades=0))
    assert not EwtImpulseFilter().evaluate(short, _sig()).passed


def test_real_engine_smoke_returns_result():
    # No mocks — the real pure engine must run end-to-end without error.
    res = EwtImpulseFilter(pivot_period=3).evaluate(_ctx(140), _sig("long"))
    assert isinstance(res.passed, bool) and res.reason


def test_registered():
    assert build_filter("ewt_impulse") is not None
