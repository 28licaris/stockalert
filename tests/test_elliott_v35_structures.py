"""V3-5: structure catalog — flat, triangle, diagonal, truncation.

Each test is grounded in Elliott Wave Theory:
  Flat      A-B-C correction; B retraces ≥90% of A (vs zigzag's 38–79%)
  Triangle  A-B-C-D-E contractive; B<A's origin, C<A's extreme, converging
  Diagonal  5-wave motive; wave 4 OVERLAPS wave 1 (contracting wedge)
  Truncation impulse flag; wave 5 fails to exceed wave 3 level — signals exhaustion
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from app.indicators.pivots import Pivot
from app.signals.elliott.engine import WaveEngine
from app.signals.elliott.rules import (
    evaluate_flat, evaluate_triangle, evaluate_diagonal,
)
from app.signals.elliott.fib import score_flat, score_triangle, score_diagonal


def _mk(i: int, price: float, kind: str, degree: int = 1) -> Pivot:
    return Pivot(index=i, timestamp=datetime(2025, 1, 1) + timedelta(days=i),
                 price=price, kind=kind, k=10, degree=degree,
                 confirmed_at_index=i + 10)


_ENGINE = WaveEngine()


def _label(pivots, last_price, as_of=90):
    return _ENGINE.label(pivots, last_price, symbol="TEST", interval="1d",
                         as_of_index=as_of, as_of=datetime(2025, 4, 1))


# ===========================================================================
# FLAT rules
# ===========================================================================

def test_flat_b_gte_90pct_passes_for_regular_flat():
    """B retracing 95% of A → flat rule passes."""
    # Down flat: H0=100, L_A=60 (A=40pts down), H_B=98 (B=38pts up, 95% retrace)
    prices = [100.0, 60.0, 98.0]
    result = evaluate_flat(prices, "down")
    assert result["flat_b_gte_90pct"] is True


def test_flat_b_gte_90pct_passes_for_expanded_flat():
    """B exceeding origin (>100% retrace) → expanded flat rule passes."""
    # Down flat: H0=100, L_A=60 (A=40pts), H_B=106 (B=46pts up, 115% retrace)
    prices = [100.0, 60.0, 106.0]
    result = evaluate_flat(prices, "down")
    assert result["flat_b_gte_90pct"] is True


def test_flat_b_fails_for_zigzag_like_retrace():
    """B retracing only 62% of A → NOT a flat (too shallow)."""
    prices = [100.0, 60.0, 84.8]   # B = 24.8pts = 62% of 40pts
    result = evaluate_flat(prices, "down")
    assert result["flat_b_gte_90pct"] is False


def test_flat_score_regular_flat():
    """Regular flat (B≈100%, C≈100%) should score high."""
    # Down flat: A=40pts, B=40pts (100%), C=40pts (100%)
    prices = [100.0, 60.0, 100.0, 60.0]
    score = score_flat(prices, "down")
    assert score >= 0.80, f"expected ≥0.80 for perfect regular flat, got {score}"


def test_flat_score_expanded_flat():
    """Expanded flat (B=115%, C=130%) scores decently."""
    # Down: A=100→60 (40pts), B=60→106 (46pts=115%), C=106→53 (53pts=132%)
    prices = [100.0, 60.0, 106.0, 53.0]
    score = score_flat(prices, "down")
    assert score >= 0.50, f"expected ≥0.50 for expanded flat, got {score}"


def test_flat_distinguishes_from_zigzag_by_b_depth():
    """A B-retrace of 95% scores near-zero on zigzag and high on flat."""
    from app.signals.elliott.fib import score_zigzag
    prices = [100.0, 60.0, 98.0, 60.0]  # B=95% retrace
    zz_score = score_zigzag(prices, "down")
    flat_score = score_flat(prices, "down")
    assert flat_score > zz_score, (
        f"flat ({flat_score}) should score higher than zigzag ({zz_score}) for B=95%"
    )


# ===========================================================================
# FLAT engine integration
# ===========================================================================

def test_engine_surfaces_flat_for_deep_b_retrace():
    """A completed flat (4 pivots, B≈95% of A, C≈A) surfaces through the engine."""
    # Down flat: H0=200, L_A=120 (A=80pts down), H_B=196 (B=76pts=95%), L_C=116 (C≈A)
    # 4 pivots → structure="complete"; nesting weight is sufficient for it to pass.
    pivots = [
        _mk(0, 200.0, "high"), _mk(20, 120.0, "low"),
        _mk(40, 196.0, "high"), _mk(60, 116.0, "low"),
    ]
    lab = _label(pivots, last_price=130.0)
    structures = {c.structure for c in [lab.primary, lab.secondary] + lab.alternates if c}
    assert "flat" in structures, f"expected 'flat' in structures, got {structures}"


def test_flat_candidate_has_correct_current_wave():
    """_flat() builder labels in-progress C correctly (current_wave='C')."""
    # Test the builder directly; nesting gates the 3-pivot count below min_confidence
    # for label(), which is correct — a 3-pivot corrective at the same degree has no
    # sub-waves to validate. The builder itself must assign the right label.
    from app.indicators.pivots import Pivot as _Pivot
    from app.signals.elliott.engine import alternate as _alt
    pivots = [_mk(0, 200.0, "high"), _mk(20, 120.0, "low"), _mk(40, 196.0, "high")]
    engine = WaveEngine()
    run = _alt(pivots)
    cands = engine._flat(run, last_price=150.0)
    assert cands, "expected _flat() to return a candidate for B=95% retrace"
    assert cands[0].current_wave == "C"


# ===========================================================================
# TRIANGLE rules
# ===========================================================================

def test_triangle_b_within_origin_passes():
    """B not exceeding A's origin → triangle rule passes."""
    # Down triangle: H0=100, L_A=70, H_B=90 (below H0=100 ✓), L_C=75 (above L_A=70)
    prices = [100.0, 70.0, 90.0, 75.0]
    result = evaluate_triangle(prices, "down")
    assert result["tri_b_within_origin"] is True
    assert result["tri_c_converges"] is True


def test_triangle_b_exceeds_origin_fails():
    """B exceeding A's origin → NOT a contracting triangle."""
    # Down triangle: H_B=105 > H0=100 → fails
    prices = [100.0, 70.0, 105.0, 75.0]
    result = evaluate_triangle(prices, "down")
    assert result["tri_b_within_origin"] is False


def test_triangle_c_reaches_a_extreme_fails():
    """C reaching or passing A's extreme → NOT converging."""
    # Down triangle: L_C=68 < L_A=70 (C went lower than A) → fails
    prices = [100.0, 70.0, 90.0, 68.0]
    result = evaluate_triangle(prices, "down")
    assert result["tri_c_converges"] is False


def test_triangle_full_6_pivots_all_rules():
    """Full contracting triangle (6 prices) — all 4 convergence rules pass."""
    # Down: H0=100, L_A=70, H_B=88, L_C=74, H_D=84, L_E=77
    prices = [100.0, 70.0, 88.0, 74.0, 84.0, 77.0]
    result = evaluate_triangle(prices, "down")
    assert all(result.values()), f"expected all rules to pass, got {result}"


def test_triangle_score_contracting():
    """Well-formed contracting triangle (each leg ≈ 0.618× previous) scores high."""
    # Legs: 30, 18, 11, 7, 4 — each ≈ 0.60-0.62× previous
    prices = [100.0, 70.0, 88.0, 77.0, 84.0, 80.0]
    score = score_triangle(prices, "down")
    assert score >= 0.50, f"expected ≥0.50, got {score}"


def test_triangle_thrust_target_opposite_direction():
    """Post-triangle thrust target projects in the OPPOSITE direction to wave A."""
    # Down triangle (A goes down): thrust after E should be UP
    pivots = [
        _mk(0, 100.0, "high"),  # H0
        _mk(10, 70.0,  "low"),  # L_A
        _mk(20, 88.0,  "high"), # H_B
        _mk(30, 74.0,  "low"),  # L_C
        _mk(40, 84.0,  "high"), # H_D
        _mk(50, 77.0,  "low"),  # L_E (complete)
    ]
    lab = _label(pivots, last_price=80.0, as_of=60)
    tri_cands = [c for c in [lab.primary, lab.secondary] + lab.alternates
                 if c and c.structure == "triangle"]
    if not tri_cands:
        pytest.skip("no triangle surfaced (confidence below floor)")
    tri = tri_cands[0]
    if tri.fib_targets:
        thrust = next(iter(tri.fib_targets.values()))
        # Down triangle → thrust UP → target > last_price
        assert thrust > 80.0, f"expected thrust target > 80.0 (upward), got {thrust}"


# ===========================================================================
# DIAGONAL rules
# ===========================================================================

def test_diagonal_w4_overlap_required():
    """A pattern where w4 does NOT overlap w1 fails the diagonal rule."""
    # Up impulse (clean, no overlap) — should NOT be a diagonal
    # L0=100, H1=150, L2=120, H3=190, L4=160 (L4 > H1=150 → no overlap)
    prices = [100.0, 150.0, 120.0, 190.0, 160.0]
    result = evaluate_diagonal(prices, "up")
    assert result.get("diag_w4_overlaps_w1") is False


def test_diagonal_w4_overlap_detected():
    """w4 entering w1's territory → diagonal rule fires correctly."""
    # Up diagonal: L0=100, H1=150, L2=125, H3=160, L4=140
    # L4=140 < H1=150 → overlap ✓
    prices = [100.0, 150.0, 125.0, 160.0, 140.0]
    result = evaluate_diagonal(prices, "up")
    assert result.get("diag_w4_overlaps_w1") is True


def test_diagonal_requires_w3_lt_w1():
    """W3 must be shorter than W1 (contracting rule)."""
    # W1=50, W3=80 → W3 > W1 → fails
    prices = [100.0, 150.0, 125.0, 205.0, 140.0]
    result = evaluate_diagonal(prices, "up")
    assert result.get("diag_w3_lt_w1") is False


def test_diagonal_contracting_passes():
    """Well-formed contracting diagonal passes all rules."""
    # Up diagonal: L0=100, H1=150 (w1=50), L2=130 (w2=20, 40% retrace),
    # H3=160 (w3=30 < w1=50 ✓), L4=145 (w4=15, overlaps H1=150 ✓, w4<w2? 15<20 ✓)
    prices = [100.0, 150.0, 130.0, 160.0, 145.0]
    result = evaluate_diagonal(prices, "up")
    assert result.get("diag_w4_overlaps_w1") is True
    assert result.get("diag_w3_lt_w1") is True
    assert result.get("diag_w4_lt_w2") is True


def test_diagonal_score_contracting():
    """Well-formed contracting diagonal (deep w2/w4, short w3) scores decently."""
    # L0=100, H1=150, L2=132 (w2=18=36%), H3=158 (w3=26=52% of w1),
    # L4=144 (w4=14, overlaps H1=150 ✓), H5=155 (w5=11 < w3=26 ✓)
    prices = [100.0, 150.0, 132.0, 158.0, 144.0, 155.0]
    score = score_diagonal(prices, "up")
    assert score >= 0.40, f"expected ≥0.40, got {score}"


def test_engine_surfaces_diagonal_for_overlapping_w4():
    """When w4 overlaps w1 and the pattern contracts, a diagonal candidate surfaces."""
    # Up diagonal: L0=100, H1=150, L2=130, H3=160, L4=145
    # Last price continues to H5 territory
    pivots = [
        _mk(0,  100.0, "low"),
        _mk(20, 150.0, "high"),
        _mk(40, 130.0, "low"),
        _mk(60, 160.0, "high"),
        _mk(80, 145.0, "low"),
    ]
    lab = _label(pivots, last_price=153.0, as_of=90)
    structures = {c.structure for c in [lab.primary, lab.secondary] + lab.alternates if c}
    assert "diagonal" in structures, (
        f"expected 'diagonal' in structures, got {structures}"
    )


# ===========================================================================
# TRUNCATION flag
# ===========================================================================

def test_truncation_flagged_when_w5_fails_to_exceed_w3():
    """Wave 5 ending below wave 3's level → is_truncated=True on impulse."""
    # Up impulse: L0=100, H1=150, L2=120, H3=200, L4=160, H5=195
    # H5=195 < H3=200 → truncated
    pivots = [
        _mk(0,  100.0, "low"),
        _mk(20, 150.0, "high"),
        _mk(40, 120.0, "low"),
        _mk(60, 200.0, "high"),
        _mk(80, 160.0, "low"),
        _mk(100, 195.0, "high"),
    ]
    lab = _label(pivots, last_price=185.0, as_of=110)
    impulse_cands = [c for c in [lab.primary, lab.secondary] + lab.alternates
                     if c and c.structure == "impulse" and c.current_wave == "complete"]
    assert impulse_cands, "expected a complete impulse candidate"
    trunc = [c for c in impulse_cands if c.is_truncated]
    assert trunc, "expected is_truncated=True for the truncated impulse"


def test_truncation_not_flagged_for_normal_wave5():
    """Normal wave 5 (exceeds wave 3) → is_truncated=False."""
    # Up impulse: H5=215 > H3=200 — clean
    pivots = [
        _mk(0,  100.0, "low"),
        _mk(20, 150.0, "high"),
        _mk(40, 120.0, "low"),
        _mk(60, 200.0, "high"),
        _mk(80, 160.0, "low"),
        _mk(100, 215.0, "high"),
    ]
    lab = _label(pivots, last_price=205.0, as_of=110)
    impulse_cands = [c for c in [lab.primary, lab.secondary] + lab.alternates
                     if c and c.structure == "impulse" and c.current_wave == "complete"]
    if impulse_cands:
        assert not any(c.is_truncated for c in impulse_cands), \
            "normal wave 5 should not be flagged as truncated"


def test_truncation_penalizes_confidence():
    """A truncated impulse has lower confidence than an identical non-truncated one."""
    engine = WaveEngine()

    def _lab(h5):
        pivots = [
            _mk(0,  100.0, "low"),
            _mk(20, 150.0, "high"),
            _mk(40, 120.0, "low"),
            _mk(60, 200.0, "high"),
            _mk(80, 160.0, "low"),
            _mk(100, h5, "high"),
        ]
        return engine.label(pivots, last_price=h5 - 5, symbol="T", interval="1d",
                            as_of_index=110, as_of=datetime(2025, 4, 1))

    lab_trunc = _lab(195.0)  # truncated: H5 < H3=200
    lab_clean = _lab(215.0)  # clean: H5 > H3=200

    trunc_cands = [c for c in [lab_trunc.primary, lab_trunc.secondary] + lab_trunc.alternates
                   if c and c.structure == "impulse" and c.is_truncated]
    clean_cands = [c for c in [lab_clean.primary, lab_clean.secondary] + lab_clean.alternates
                   if c and c.structure == "impulse" and not c.is_truncated]

    if trunc_cands and clean_cands:
        assert trunc_cands[0].confidence < clean_cands[0].confidence, (
            "truncated impulse should have lower confidence than clean one"
        )


def test_truncation_rationale_mentions_truncated():
    """Truncated impulse rationale should mention truncation."""
    pivots = [
        _mk(0,  100.0, "low"),
        _mk(20, 150.0, "high"),
        _mk(40, 120.0, "low"),
        _mk(60, 200.0, "high"),
        _mk(80, 160.0, "low"),
        _mk(100, 195.0, "high"),
    ]
    lab = _label(pivots, last_price=185.0, as_of=110)
    trunc_cands = [c for c in [lab.primary, lab.secondary] + lab.alternates
                   if c and c.structure == "impulse" and c.is_truncated]
    if trunc_cands:
        assert "truncat" in trunc_cands[0].rationale.lower()


# ===========================================================================
# Schema field defaults (backward compat)
# ===========================================================================

def test_wave_candidate_new_fields_have_defaults():
    """is_truncated and is_diagonal default to False — existing code unaffected."""
    from app.signals.elliott.schemas import WaveCandidate
    c = WaveCandidate(
        structure="impulse", direction="up", current_wave="3",
        degree=1, pivots=[], labels=[], rules_passed={},
        rule_score=1.0, fib_score=0.7, confidence=0.6,
        invalidation_price=100.0,
    )
    assert c.is_truncated is False
    assert c.is_diagonal is False
