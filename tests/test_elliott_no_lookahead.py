"""Gate zero: a label produced at bar T must NOT change when future bars
arrive. This is the single most important property of the engine — it is what
separates an honest wave count from hindsight-fitted backtest alpha."""
from __future__ import annotations

from app.indicators.pivots import PivotDetector
from app.signals.elliott import WaveEngine
from tests._ewt_synthetic import AS_OF_WAVE3, synthetic_ohlc


def _label_with_data_through(through: int, as_of: int):
    close, high, low = synthetic_ohlc("up")
    c, h, l = close.iloc[:through + 1], high.iloc[:through + 1], low.iloc[:through + 1]
    piv = PivotDetector(period=3, source="hl").detect(c, h, l)
    eng = WaveEngine()
    return eng.label(piv, last_price=float(close.iloc[as_of]), symbol="TEST",
                     interval="1d", as_of_index=as_of,
                     as_of=close.index[as_of].to_pydatetime())


def test_label_stable_when_future_bars_added():
    now = _label_with_data_through(AS_OF_WAVE3, AS_OF_WAVE3)
    future = _label_with_data_through(AS_OF_WAVE3 + 10, AS_OF_WAVE3)
    assert now.model_dump() == future.model_dump()


def test_unconfirmed_pivot_invisible():
    as_of = AS_OF_WAVE3
    close, high, low = synthetic_ohlc("up")
    piv = PivotDetector(period=3, source="hl").detect(close, high, low)
    lab = WaveEngine().label(piv, last_price=float(close.iloc[as_of]), symbol="TEST",
                             interval="1d", as_of_index=as_of,
                             as_of=close.index[as_of].to_pydatetime())
    used = [p for c in ([lab.primary, lab.secondary] + lab.alternates) if c for p in c.pivots]
    assert all(p.confirmed_at_index <= as_of for p in used)
