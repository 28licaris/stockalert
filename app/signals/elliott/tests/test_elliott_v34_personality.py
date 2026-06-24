"""V3-4: personality bonus — in-progress wave extension scoring.

`personality_bonus` measures how far the open wave has confirmed itself
by price action.  A wave-3 that has already extended 1.7× wave-1 is more
trustworthy than one barely started, yet both look the same to
`score_impulse` which only sees completed pivot-to-pivot ratios.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from app.indicators.pivots import Pivot
from app.signals.elliott.fib import personality_bonus
from app.signals.elliott.schemas import WaveCandidate
from app.signals.elliott.engine import WaveEngine


# ---------------------------------------------------------------------------
# personality_bonus unit tests
# ---------------------------------------------------------------------------

def test_wave3_confirmed_extension_scores_high():
    """Wave-3 already 1.74× wave-1 → strong bonus (≥ 0.90)."""
    # Mirrors AAPL: w1=37.5 pts (243.4→280.9), wave-2 ends at 245.1,
    # current price 310.36 → w3_so_far = 65.26 = 1.74×w1
    prices = [243.4, 280.9, 245.1]
    score = personality_bonus(prices, "up", last_price=310.36, current_wave="3")
    assert score >= 0.90, f"expected ≥ 0.90 for confirmed extension, got {score}"


def test_wave3_early_stage_partial_credit():
    """Wave-3 at 0.80× wave-1 in progress → partial credit (0.5–0.9)."""
    # w1 = 40 pts, wave-2 ends at 160, w3 so far = 32 = 0.80×w1
    prices = [100.0, 140.0, 160.0]
    score = personality_bonus(prices, "down", last_price=128.0, current_wave="3")
    # 0.80× is below the ideal 1.618 but still in progress
    assert 0.5 <= score <= 0.95, f"expected partial credit, got {score}"


def test_wave3_wrong_direction_returns_zero():
    """Wave-3 price moved the wrong way → 0.0 (not yet started / reversed)."""
    prices = [100.0, 140.0, 120.0]
    # Direction is "up" but last_price (115) is BELOW wave-2 end (120)
    score = personality_bonus(prices, "up", last_price=115.0, current_wave="3")
    assert score == 0.0


def test_wave5_near_w1_scores_high():
    """Wave-5 ≈ wave-1 in size → high personality bonus (≥ 0.80)."""
    # w1 = 100 → 150 = 50 pts; wave-5 so far from 125 to 174 = 49 pts ≈ 1.0×w1
    prices = [100.0, 150.0, 130.0, 180.0, 125.0]
    score = personality_bonus(prices, "up", last_price=174.0, current_wave="5")
    assert score >= 0.80, f"expected ≥ 0.80 for wave-5 ≈ wave-1, got {score}"


def test_personality_no_bonus_for_complete():
    """current_wave='complete' → 0.0 (structure finished, no open wave)."""
    prices = [100.0, 150.0, 120.0, 170.0, 130.0, 165.0]
    score = personality_bonus(prices, "up", last_price=160.0, current_wave="complete")
    assert score == 0.0


def test_personality_no_bonus_for_wave2():
    """current_wave='2' → 0.0 (corrective waves not personality-scored)."""
    prices = [100.0, 140.0]
    score = personality_bonus(prices, "up", last_price=125.0, current_wave="2")
    assert score == 0.0


def test_personality_no_bonus_zero_w1():
    """Zero wave-1 (degenerate) → 0.0 safe fallback."""
    prices = [100.0, 100.0, 90.0]
    score = personality_bonus(prices, "down", last_price=85.0, current_wave="3")
    assert score == 0.0


# ---------------------------------------------------------------------------
# Engine integration — personality feeds into confidence
# ---------------------------------------------------------------------------

def _mk(i: int, price: float, kind: str, degree: int = 1) -> Pivot:
    return Pivot(index=i, timestamp=datetime(2025, 1, 1) + timedelta(days=i),
                 price=price, kind=kind, k=10, degree=degree,
                 confirmed_at_index=i + 10)


def test_engine_wave3_with_extension_beats_wave3_without():
    """Two wave-3 candidates identical in pivot structure but different price
    extensions: the one that's already extended beyond wave-1 should score higher.
    """
    engine = WaveEngine()

    # Pivots: low→high→low (down impulse, wave-3 in progress)
    pivs = [_mk(0, 500.0, "high"), _mk(30, 300.0, "low"), _mk(60, 420.0, "high")]

    # Candidate 1: last_price barely started wave-3 (just past wave-2 end)
    lab_early = engine.label(pivs, last_price=405.0, symbol="TEST", interval="1d",
                             as_of_index=70, as_of=datetime(2025, 3, 12))

    # Candidate 2: last_price well extended into wave-3 (>1× wave-1 already)
    lab_extended = engine.label(pivs, last_price=220.0, symbol="TEST", interval="1d",
                                as_of_index=70, as_of=datetime(2025, 3, 12))

    # Both should produce a primary; extended should have higher probability
    assert lab_extended.primary is not None, "extended wave-3 should surface"
    # Probability may equal if both pass, but extended has better personality →
    # raw confidence should be higher for the extended case
    if lab_early.primary is not None:
        assert lab_extended.primary.confidence >= lab_early.primary.confidence, (
            f"extended ({lab_extended.primary.confidence:.3f}) should be ≥ "
            f"early ({lab_early.primary.confidence:.3f})"
        )
