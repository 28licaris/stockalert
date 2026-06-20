"""Elliott's three inviolable rules, as pure direction-aware predicates.

A labeling that breaks any of these is invalid — the engine discards it. The
predicates operate on the ordered *prices* of an impulse skeleton:

    up-impulse   prices = [L0, H1, L2, H3, L4, H5]   (a prefix is fine)
    down-impulse prices = [H0, L1, H2, L3, H4, L5]

Only the legs that exist are checked, so an in-progress count (3, 4, or 5
prices) is evaluated against the rules that can apply so far. `rule2` is
provisional until wave 5 prints (you cannot know wave 3 is "not the shortest of
1/3/5" before wave 5 exists), so before then we enforce the weaker, always-true
necessary condition `|w3| >= |w1|`.
"""
from __future__ import annotations

from typing import Literal

Direction = Literal["up", "down"]


def _sign(direction: Direction) -> int:
    return 1 if direction == "up" else -1


def rule1_wave2_no_full_retrace(prices: list[float], direction: Direction) -> bool:
    """Wave 2 never retraces more than 100% of wave 1."""
    s = _sign(direction)
    return (prices[2] - prices[0]) * s > 0


def rule2_wave3_not_shortest(prices: list[float], direction: Direction) -> bool:
    """Wave 3 is never the shortest of waves 1/3/5. Provisional (>= w1) until
    wave 5 exists."""
    w1 = abs(prices[1] - prices[0])
    w3 = abs(prices[3] - prices[2])
    if len(prices) >= 6:
        w5 = abs(prices[5] - prices[4])
        return not (w3 < w1 and w3 < w5)
    return w3 >= w1


def rule3_wave4_no_overlap(prices: list[float], direction: Direction) -> bool:
    """Wave 4 never enters wave 1's price territory (non-diagonal impulses)."""
    s = _sign(direction)
    return (prices[4] - prices[1]) * s > 0


def evaluate_impulse(prices: list[float], direction: Direction) -> dict[str, bool]:
    """Evaluate every rule that applies given how many prices we have."""
    out: dict[str, bool] = {}
    if len(prices) >= 3:
        out["rule1_w2_no_full_retrace"] = rule1_wave2_no_full_retrace(prices, direction)
    if len(prices) >= 4:
        out["rule2_w3_not_shortest"] = rule2_wave3_not_shortest(prices, direction)
    if len(prices) >= 5:
        out["rule3_w4_no_overlap"] = rule3_wave4_no_overlap(prices, direction)
    return out


def evaluate_flat(prices: list[float], direction: Direction) -> dict[str, bool]:
    """Flat A-B-C correction. Distinguishing rule: wave B retraces ≥90% of wave A.

    In a zigzag B retraces 38–79%; in a flat B retraces 90%+ (regular flat
    ≈100%, expanded flat >100%). Both A and C move in the same direction.

        down-flat prices = [H0, LA, HB, LC]  — A down, B up ≥90%, C down
        up-flat   prices = [L0, HA, LB, HC]  — A up, B down ≥90%, C up
    """
    out: dict[str, bool] = {}
    if len(prices) >= 3:
        a = abs(prices[1] - prices[0])
        b_ret = abs(prices[2] - prices[1]) / a if a > 0 else 0.0
        out["flat_b_gte_90pct"] = b_ret >= 0.90
    return out


def evaluate_triangle(prices: list[float], direction: Direction) -> dict[str, bool]:
    """Contracting triangle A-B-C-D-E: converging trendlines, all corrective.

    Each successive wave falls short of the previous wave's extreme — the highs
    get lower and the lows get higher (for a down-first triangle). B must not
    exceed A's origin; C must not reach A's extreme; D must not exceed B; E must
    not reach C's extreme.

        down-triangle prices = [H0, LA, HB, LC, HD, LE]
        up-triangle   prices = [L0, HA, LB, HC, LD, HE]
    `direction` = direction of first wave A (same convention as zigzag/flat).

    NOTE: triangles do NOT have a wave-4/wave-1 overlap rule — that belongs to
    diagonals. Triangles are corrective; all internal waves are 3-wave structures.
    """
    s = _sign(direction)
    out: dict[str, bool] = {}
    if len(prices) >= 4:
        # B must not exceed A's origin (trendline convergence on A's side)
        out["tri_b_within_origin"] = (prices[2] - prices[0]) * s > 0
        # C must not reach A's extreme (trendline convergence on the other side)
        out["tri_c_converges"] = (prices[3] - prices[1]) * s < 0
    if len(prices) >= 5:
        # D must not exceed B (upper trendline continues contracting)
        out["tri_d_within_b"] = (prices[4] - prices[2]) * s > 0
    if len(prices) >= 6:
        # E must not reach C's extreme (lower trendline continues contracting)
        out["tri_e_converges"] = (prices[5] - prices[3]) * s < 0
    return out


def evaluate_diagonal(prices: list[float], direction: Direction) -> dict[str, bool]:
    """Contracting diagonal: 5-wave motive structure where wave 4 overlaps wave 1.

    This is the EWT exception to rule 3. Unlike impulses (w4 never in w1 territory)
    a diagonal REQUIRES that overlap — it is the defining structural feature.
    All sub-waves are 3-wave corrective structures internally. The wave sizes
    must contract: w3 < w1, w4 < w2, w5 < w3 (converging wedge).

    Rule 1 (w2 never fully retraces w1) still holds.

        up-diagonal   prices = [L0, H1, L2, H3, L4, H5]  — w4 (L4) overlaps w1 (H1)
        down-diagonal prices = [H0, L1, H2, L3, H4, L5]  — w4 (H4) overlaps w1 (L1)
    """
    s = _sign(direction)
    out: dict[str, bool] = {}
    if len(prices) >= 3:
        out["diag_rule1_w2_no_full_retrace"] = rule1_wave2_no_full_retrace(prices, direction)
    if len(prices) >= 4:
        w1 = abs(prices[1] - prices[0])
        w3 = abs(prices[3] - prices[2])
        out["diag_w3_lt_w1"] = (w3 < w1) if w1 > 0 else False
    if len(prices) >= 5:
        # REQUIRED: w4 MUST overlap w1 — this is what makes it a diagonal
        out["diag_w4_overlaps_w1"] = (prices[4] - prices[1]) * s < 0
        w2 = abs(prices[2] - prices[1])
        w4 = abs(prices[4] - prices[3])
        out["diag_w4_lt_w2"] = (w4 < w2) if w2 > 0 else False
    if len(prices) >= 6:
        w3 = abs(prices[3] - prices[2])
        w5 = abs(prices[5] - prices[4])
        out["diag_w5_lt_w3"] = (w5 < w3) if w3 > 0 else False
    return out


def evaluate_zigzag(prices: list[float], direction: Direction) -> dict[str, bool]:
    """Zigzag A-B-C. The structural constraint we enforce: wave B does not
    retrace beyond the origin of wave A (else it is not a zigzag).

        down-zigzag prices = [H0, LA, HB, LC]  → HB must stay below H0
        up-zigzag   prices = [L0, HA, LB, HC]  → LB must stay above L0
    `direction` is the direction of the *correction* (down = a-b-c down)."""
    out: dict[str, bool] = {}
    s = _sign(direction)
    if len(prices) >= 3:
        # B (prices[2]) must not retrace past A's origin (prices[0]). For an
        # up-correction (s=+1) that means LB stays above L0; for a down one,
        # HB stays below H0. Both: (prices[2] - prices[0]) * s > 0.
        out["zz_b_no_origin_overshoot"] = (prices[2] - prices[0]) * s > 0
    return out
