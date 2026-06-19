"""EW-2: the WaveEngine — in-progress labeling, scoring, determinism."""
from __future__ import annotations

from app.indicators.pivots import PivotDetector
from app.signals.elliott import WaveEngine, fib
from tests._ewt_synthetic import AS_OF_WAVE3, synthetic_ohlc


def _label(direction="up", as_of=AS_OF_WAVE3):
    close, high, low = synthetic_ohlc(direction)
    piv = PivotDetector(period=3, source="hl").detect(close, high, low)
    eng = WaveEngine()
    return eng.label(piv, last_price=float(close.iloc[as_of]),
                     symbol="TEST", interval="1d", as_of_index=as_of,
                     as_of=close.index[as_of].to_pydatetime())


def test_in_progress_wave3_up():
    lab = _label("up")
    assert lab.primary is not None
    assert lab.primary.structure == "impulse"
    assert lab.primary.direction == "up"
    assert lab.primary.current_wave == "3"
    assert lab.primary.confidence >= 0.5
    assert lab.primary.rule_score == 1.0


def test_in_progress_wave3_down():
    lab = _label("down")
    assert lab.primary is not None
    assert lab.primary.structure == "impulse"
    assert lab.primary.direction == "down"
    assert lab.primary.current_wave == "3"


def test_anchored_target_and_invalidation():
    lab = _label("up")
    # wave-3 target projects from the wave-2 low, ~1.618*|w1| above it
    t = lab.primary.fib_targets
    assert any("1.618" in k for k in t)
    # invalidation is the wave-2 low (the stop), strictly below current price
    assert lab.primary.invalidation_price < lab.as_of_price


def test_secondary_is_a_real_alternate():
    lab = _label("up")
    others = [c for c in ([lab.secondary] + lab.alternates) if c]
    assert any(c.structure == "zigzag" for c in others)


def test_probabilities_and_uncertainty_sum_to_one():
    lab = _label("up")
    surfaced = sum(c.probability for c in (lab.primary, lab.secondary) if c)
    assert abs(surfaced + lab.uncertainty - 1.0) < 1e-6
    assert lab.uncertainty > 0  # honesty: never 100% certain


def test_determinism_byte_identical():
    a, b = _label("up"), _label("up")
    assert a.model_dump() == b.model_dump()


def test_targets_helper_anchors_correctly():
    # wave-3 target = wave-2 low + 1.618 * |w1|, NOT 1.618 * price
    t = fib.impulse_targets([100.0, 120.0, 108.0], "up", 3)
    assert abs(t["w3=1.618xW1"] - (108 + 1.618 * 20)) < 0.01
