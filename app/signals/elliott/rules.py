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
