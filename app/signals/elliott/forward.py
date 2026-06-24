"""Forward projection (V3-2) — the trader-facing "what's next".

A pro's output is not "you are in wave 4"; it is "you are in wave 4, the next
move is wave 5 DOWN into THIS zone, invalidated at THIS line." This module turns
the current wave into the projection of the wave you are moving INTO, as a
Fibonacci **confluence zone** (a range where ≥2 ratios cluster) plus the
structural invalidation.

Anchored Fib math (the classic mistake is projecting from the wrong point):
in-progress motive waves project from the prior corrective termination; when in
a corrective wave we project the *next* motive wave from that correction's
expected end. Pure + deterministic; no instrument-specific logic.
"""
from __future__ import annotations

from typing import Optional


def _sign(direction: str) -> int:
    return 1 if direction == "up" else -1


def project_forward(prices: list[float], direction: str, structure: str,
                    current_wave: str) -> Optional[dict]:
    """Return the forward plan for an impulse count, or None if not projectable.

    {next_move, target_low, target_high, target_basis[], invalidation}
    """
    if structure != "impulse" or len(prices) < 2:
        return None
    s = _sign(direction)
    w1 = abs(prices[1] - prices[0])
    if w1 <= 0:
        return None
    trend = "down" if s < 0 else "up"
    counter = "up" if s < 0 else "down"

    levels: list[tuple[str, float]] = []
    invalidation: Optional[float] = None
    next_move: Optional[str] = None

    if current_wave == "2":
        # in wave 2 → the next move is wave 3 (with-trend), the biggest leg.
        w2_end = prices[1] - s * 0.618 * w1                 # assume ~.618 retrace
        levels = [("wave 3 = 1.618×W1", w2_end + s * 1.618 * w1),
                  ("wave 3 = 2.618×W1", w2_end + s * 2.618 * w1)]
        invalidation = prices[0]                            # W1 origin
        next_move = f"wave 3 {trend}"
    elif current_wave == "3" and len(prices) >= 3:
        anchor = prices[2]                                  # project W3 from W2 end
        levels = [("1.618×W1", anchor + s * 1.618 * w1),
                  ("2.618×W1", anchor + s * 2.618 * w1)]
        invalidation = prices[2]
        next_move = f"wave 3 target {trend}"
    elif current_wave == "4" and len(prices) >= 4:
        # in wave 4 → the actionable move is wave 5 (with-trend), AFTER the bounce.
        w3 = abs(prices[3] - prices[2])
        net = abs(prices[3] - prices[0])
        w4_end = prices[3] - s * 0.382 * w3                 # assume ~.382 retrace
        levels = [("wave 5 = W1", w4_end + s * w1),
                  ("wave 5 = 0.618×(W1→3)", w4_end + s * 0.618 * net)]
        invalidation = prices[1]                            # W1 territory (rule 3)
        next_move = f"wave 5 {trend}"
    elif current_wave == "5" and len(prices) >= 5:
        anchor = prices[4]                                  # project W5 from W4 end
        levels = [("= W1", anchor + s * w1),
                  ("1.618×W1", anchor + s * 1.618 * w1)]
        invalidation = prices[4]
        next_move = "wave 5 target (impulse completion)"
    elif current_wave == "complete":
        # impulse done → expect an A-B-C correction against the trend.
        net = abs(prices[-1] - prices[0])
        top = prices[-1]
        levels = [(".382 retrace", top - s * 0.382 * net),
                  (".618 retrace", top - s * 0.618 * net)]
        invalidation = prices[-1]                           # beyond the impulse extreme
        next_move = f"A-B-C correction {counter}"

    if not levels:
        return None
    prices_only = [p for _, p in levels]
    return {
        "next_move": next_move,
        "target_low": round(min(prices_only), 2),
        "target_high": round(max(prices_only), 2),
        "target_basis": [lbl for lbl, _ in levels],
        "invalidation": round(invalidation, 2) if invalidation is not None else None,
    }
