"""V3-6: degree coherence — recency + wave-size scoring.

`degree_coherence_score` prefers counts whose pivots sit in the recent portion
of the bar history and whose individual wave sizes are modest relative to the
current price.  This causes a finer-degree recent count to rank above a macro
count with the same raw Fibonacci fit.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.indicators.pivots import Pivot
from app.signals.elliott.nesting import apply_nesting, degree_coherence_score
from app.signals.elliott.schemas import WaveCandidate


def _mk(i: int, price: float, kind: str, degree: int = 0) -> Pivot:
    return Pivot(
        index=i, timestamp=datetime(2025, 1, 1) + timedelta(days=i),
        price=price, kind=kind, k=5, degree=degree, confirmed_at_index=i + 5,
    )


def _cand(degree: int, pivots: list[Pivot], conf: float = 0.65) -> WaveCandidate:
    return WaveCandidate(
        structure="impulse", direction="down", current_wave="3", degree=degree,
        pivots=pivots, labels=["0", "1", "2", "3"][: len(pivots)],
        rules_passed={"r": True}, rule_score=1.0, fib_score=0.75,
        confidence=conf, invalidation_price=pivots[0].price,
    )


# ---------------------------------------------------------------------------
# degree_coherence_score unit tests
# ---------------------------------------------------------------------------

def test_coherence_recent_small_waves():
    """Recent pivots with small waves → high coherence (≥ 0.75)."""
    # Pivots at bars 180-230 of a 250-bar history; waves span ~9 % of price
    pivots = [_mk(180, 4918, "high", 1), _mk(210, 4510, "low", 1), _mk(230, 4783, "high", 1)]
    c = _cand(1, pivots)
    score = degree_coherence_score(c, last_price=4600.0, as_of_index=250)
    assert score >= 0.75, f"expected ≥ 0.75, got {score}"


def test_coherence_old_large_waves():
    """Pivots at the start of a 250-bar window with 34 % price swings → low (< 0.40)."""
    # Wave-1 spans 1 527 pts on ~4 400 price (34 %) — mirrors the /GC degree-2 problem
    pivots = [_mk(0, 5627, "high", 2), _mk(50, 4100, "low", 2), _mk(100, 4918, "high", 2)]
    c = _cand(2, pivots)
    score = degree_coherence_score(c, last_price=4400.0, as_of_index=250)
    assert score < 0.40, f"expected < 0.40, got {score}"


def test_coherence_size_penalty_kicks_in_at_30pct():
    """Wave spanning 40 % of price triggers the size penalty."""
    # Single wave: 1000 → 1400 = 400 pts / 1000 = 40 %
    pivots = [_mk(100, 1000, "low", 0), _mk(150, 1400, "high", 0)]
    c = WaveCandidate(
        structure="impulse", direction="up", current_wave="1", degree=0,
        pivots=pivots, labels=["0", "1"], rules_passed={}, rule_score=1.0,
        fib_score=0.8, confidence=0.6, invalidation_price=1000.0,
    )
    # Recency: oldest bar 100 / 200 = 0.50 → recency contributes 0.65*0.50 = 0.325
    # Size: 40 % → penalty = (0.40-0.30)/0.20 = 0.5 → size_score = 0.5 → 0.35*0.5 = 0.175
    # coherence ≈ 0.50
    score = degree_coherence_score(c, last_price=1000.0, as_of_index=200)
    assert score < 0.60, f"expected < 0.60, got {score}"


def test_coherence_edge_zero_price():
    """last_price=0 → neutral score of 1.0 (safe default)."""
    pivots = [_mk(0, 100, "low"), _mk(10, 120, "high")]
    c = _cand(0, pivots)
    assert degree_coherence_score(c, last_price=0.0, as_of_index=100) == 1.0


def test_coherence_edge_zero_as_of():
    """as_of_index=0 → neutral score of 1.0 (safe default, avoids div-by-zero)."""
    pivots = [_mk(0, 100, "low"), _mk(10, 120, "high")]
    c = _cand(0, pivots)
    assert degree_coherence_score(c, last_price=100.0, as_of_index=0) == 1.0


# ---------------------------------------------------------------------------
# apply_nesting integration (backward-compat + degree selection)
# ---------------------------------------------------------------------------

def test_apply_nesting_backward_compat_defaults():
    """Old call signature (no price/as_of) → coherence neutral → nesting_score == 1.0
    for a well-proportioned single-degree count.  Existing tests unchanged."""
    pivots = [_mk(0, 100, "low"), _mk(10, 120, "high"), _mk(16, 108, "low")]
    c = _cand(0, pivots, conf=0.6)
    apply_nesting(c, {})          # old call — no price/as_of
    assert c.nesting_score == 1.0
    assert c.confidence == 0.6    # (0.6 + 0.4*1.0) * 0.6 = 0.6


def test_apply_nesting_penalises_old_coarse_count():
    """An old coarse count (oldest pivot at bar 0) should see its confidence
    discounted even if internally proportionate."""
    pivots = [_mk(0, 5627, "high", 2), _mk(50, 4100, "low", 2), _mk(100, 4918, "high", 2)]
    c = _cand(2, pivots, conf=0.68)
    apply_nesting(c, {}, last_price=4400.0, as_of_index=250)
    # nesting should be < 1.0 because coherence is low (~0.29)
    assert c.nesting_score < 0.90
    # confidence must be discounted
    assert c.confidence < 0.68


def test_degree_selection_recent_beats_old():
    """The key V3-6 regression: a slightly-lower-raw-confidence recent count
    should outrank an older coarse count after coherence is applied.

    Mirrors the /GC scenario:
      Coarse (degree-2): wave-1 spans 1 527 pts from bar 0 of 250
      Fine   (degree-1): wave-1 spans   408 pts from bar 200 of 250
    """
    price = 4600.0
    as_of = 250

    old_pivots = [_mk(0, 5627, "high", 2), _mk(50, 4100, "low", 2), _mk(100, 4918, "high", 2)]
    new_pivots = [_mk(200, 4918, "high", 1), _mk(225, 4510, "low", 1), _mk(240, 4783, "high", 1)]

    old_cand = _cand(2, old_pivots, conf=0.68)   # higher raw confidence
    new_cand = _cand(1, new_pivots, conf=0.65)   # lower raw confidence

    apply_nesting(old_cand, {}, last_price=price, as_of_index=as_of)
    apply_nesting(new_cand, {}, last_price=price, as_of_index=as_of)

    assert new_cand.confidence > old_cand.confidence, (
        f"recent degree-1 ({new_cand.confidence:.3f}) should beat "
        f"old degree-2 ({old_cand.confidence:.3f})"
    )
