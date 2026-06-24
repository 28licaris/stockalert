"""V3-1: wave nesting — subdivision scoring + confidence folding."""
from __future__ import annotations

from datetime import datetime, timedelta

from app.indicators.pivots import Pivot
from app.signals.elliott.nesting import _UNASSESSABLE, _subdivide_wave, apply_nesting
from app.signals.elliott.schemas import WaveCandidate


def _mk(i: int, price: float, kind: str, degree: int = 0) -> Pivot:
    return Pivot(index=i, timestamp=datetime(2024, 1, 1) + timedelta(days=i),
                 price=price, kind=kind, k=4, degree=degree, confirmed_at_index=i + 4)


def _cand(degree: int, pivots, conf: float = 0.6) -> WaveCandidate:
    return WaveCandidate(
        structure="impulse", direction="up", current_wave="3", degree=degree,
        pivots=pivots, labels=["0", "1", "2", "3", "4", "5"][: len(pivots)],
        rules_passed={"r": True}, rule_score=1.0, fib_score=0.8, confidence=conf,
        invalidation_price=pivots[-1].price,
    )


def test_subdivide_no_inner_is_neutral():
    score, _ = _subdivide_wave(_mk(0, 100, "low"), _mk(10, 120, "high"), [], "up", motive=True)
    assert score == _UNASSESSABLE


def test_subdivide_clean_motive_scores_high():
    inner = [_mk(4, 120, "high"), _mk(8, 112, "low"), _mk(12, 140, "high"), _mk(16, 132, "low")]
    score, info = _subdivide_wave(_mk(0, 100, "low"), _mk(20, 150, "high"), inner, "up", motive=True)
    assert score > 0.6
    assert info["n_subpivots"] == 4
    assert info["structure"] == "impulse"


def test_nesting_noop_for_single_degree():
    # degree 0 (finest) → no finer degree → nesting stays neutral, confidence unchanged
    c = _cand(0, [_mk(0, 100, "low"), _mk(5, 120, "high"), _mk(8, 108, "low")])
    apply_nesting(c, {})
    assert c.nesting_score == 1.0
    assert c.confidence == 0.6


def test_nesting_adjusts_confidence_with_finer_degree():
    piv = [_mk(0, 100, "low", 1), _mk(20, 150, "high", 1), _mk(30, 120, "low", 1)]
    c = _cand(1, piv)
    finer = [
        _mk(4, 120, "high"), _mk(8, 112, "low"), _mk(12, 140, "high"), _mk(16, 132, "low"),  # wave 1 sub
        _mk(22, 148, "high"), _mk(25, 128, "low"), _mk(28, 145, "high"),                      # wave 2 sub
    ]
    apply_nesting(c, {0: finer})
    assert c.nesting_score != 1.0          # actually computed
    assert c.subwaves                       # subdivision tree attached
    assert c.confidence <= 0.6              # folded in (boost only when nesting==1.0)


def test_nesting_is_deterministic():
    piv = [_mk(0, 100, "low", 1), _mk(20, 150, "high", 1), _mk(30, 120, "low", 1)]
    finer = [_mk(4, 120, "high"), _mk(8, 112, "low"), _mk(12, 140, "high"), _mk(16, 132, "low")]
    a, b = _cand(1, piv), _cand(1, piv)
    apply_nesting(a, {0: finer})
    apply_nesting(b, {0: finer})
    assert a.model_dump() == b.model_dump()
