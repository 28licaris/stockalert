"""Fibonacci scoring and **anchored** target/invalidation projection.

Two distinct jobs, and the distinction matters:

  * **Scoring** compares *leg lengths as ratios* (w3/w1, w2/w1, …). Ratios are
    anchor-independent, so `score_impulse` is a pure function of the leg
    magnitudes.
  * **Targets** are *price levels*, and a price level must be projected from the
    correct anchor. A wave-3 target is `wave-2 termination ± 1.618·|w1|` — NOT
    `1.618 × price`. Getting the anchor wrong is the classic Fib mistake.

`direction` is "up" or "down"; `s = +1/-1` carries it through the projections.
"""
from __future__ import annotations

from typing import Literal

Direction = Literal["up", "down"]
FIB = (0.236, 0.382, 0.5, 0.618, 0.786, 1.0, 1.272, 1.618, 2.618)


def _sign(direction: Direction) -> int:
    return 1 if direction == "up" else -1


def retrace_pct(a: float, b: float, c: float) -> float:
    """Fraction of the a→b move retraced by b→c."""
    move = abs(b - a)
    return abs(c - b) / move if move else 0.0


def nearest_fib(ratio: float) -> tuple[float, float]:
    """Closest Fib level and its absolute distance."""
    best = min(FIB, key=lambda f: abs(f - ratio))
    return best, abs(best - ratio)


def _band(x: float, lo: float, hi: float, peak: float, width: float = 0.35) -> float:
    """1.0 at `peak`, ~1.0 across [lo, hi], decaying outside. Bounded [0, 1]."""
    if lo <= x <= hi:
        return 1.0 - 0.3 * abs(x - peak) / max(peak, 1e-9)
    edge = lo if x < lo else hi
    return max(0.0, 1.0 - abs(x - edge) / width)


def score_impulse(prices: list[float], direction: Direction) -> float:
    """0..1 Fibonacci-fit over the legs that exist, plus an alternation reward.
    More confirmed structure that fits the guidelines → higher score."""
    parts: list[float] = []
    w1 = abs(prices[1] - prices[0]) if len(prices) >= 2 else 0.0

    if len(prices) >= 3 and w1:
        w2r = abs(prices[1] - prices[2]) / w1               # wave-2 retrace of wave-1
        parts.append(_band(w2r, 0.382, 0.786, 0.618))
    if len(prices) >= 4 and w1:
        w3e = abs(prices[3] - prices[2]) / w1               # wave-3 extension of wave-1
        parts.append(_band(w3e, 1.0, 2.8, 1.618, width=0.6))
    if len(prices) >= 5:
        w3 = abs(prices[3] - prices[2])
        if w3:
            w4r = abs(prices[3] - prices[4]) / w3           # wave-4 retrace of wave-3
            parts.append(_band(w4r, 0.236, 0.5, 0.382))
        # alternation: wave 2 and wave 4 should differ in depth
        if w1:
            w2r = abs(prices[1] - prices[2]) / w1
            w4r2 = abs(prices[3] - prices[4]) / max(w3, 1e-9)
            parts.append(min(1.0, abs(w2r - w4r2) * 2.0))
    if len(prices) >= 6 and w1:
        w5e = abs(prices[5] - prices[4]) / w1               # wave-5 vs wave-1
        parts.append(max(_band(w5e, 0.618, 1.0, 1.0), _band(w5e, 1.5, 1.8, 1.618)))

    return round(sum(parts) / len(parts), 4) if parts else 0.0


def score_zigzag(prices: list[float], direction: Direction) -> float:
    """0..1 fit for a zigzag: B retraces A by .382–.786; C ≈ A or 1.618·A."""
    parts: list[float] = []
    a = abs(prices[1] - prices[0]) if len(prices) >= 2 else 0.0
    if len(prices) >= 3 and a:
        br = abs(prices[1] - prices[2]) / a
        parts.append(_band(br, 0.382, 0.786, 0.5))
    if len(prices) >= 4 and a:
        c = abs(prices[3] - prices[2]) / a
        parts.append(max(_band(c, 0.8, 1.2, 1.0), _band(c, 1.5, 1.8, 1.618)))
    return round(sum(parts) / len(parts), 4) if parts else 0.0


def impulse_targets(prices: list[float], direction: Direction, wave: int) -> dict[str, float]:
    """Anchored forward price targets for the wave currently in progress."""
    s = _sign(direction)
    w1 = abs(prices[1] - prices[0]) if len(prices) >= 2 else 0.0
    out: dict[str, float] = {}
    if wave == 3 and len(prices) >= 3 and w1:
        anchor = prices[2]                                  # project wave 3 from wave-2 low
        out["w3=1.618xW1"] = round(anchor + s * 1.618 * w1, 2)
        out["w3=2.618xW1"] = round(anchor + s * 2.618 * w1, 2)
    elif wave == 5 and len(prices) >= 5 and w1:
        anchor = prices[4]                                  # project wave 5 from wave-4 low
        out["w5=1.0xW1"] = round(anchor + s * w1, 2)
        out["w5=1.618xW1"] = round(anchor + s * 1.618 * w1, 2)
    elif wave == 2 and len(prices) >= 2 and w1:
        for f in (0.5, 0.618, 0.786):                       # likely wave-2 reversal zone
            out[f"w2={f}retr"] = round(prices[1] - s * f * w1, 2)
    elif wave == 4 and len(prices) >= 4:
        w3 = abs(prices[3] - prices[2])
        for f in (0.236, 0.382):                            # likely wave-4 reversal zone
            out[f"w4={f}retr"] = round(prices[3] - s * f * w3, 2)
    return out


def impulse_invalidation(prices: list[float], direction: Direction, wave: int) -> float:
    """The price that voids the current count — i.e. the trade's stop."""
    if wave == 2:
        return round(prices[0], 2)                          # below wave-1 origin
    if wave == 3 and len(prices) >= 3:
        return round(prices[2], 2)                          # below wave-2 termination
    if wave == 4 and len(prices) >= 2:
        return round(prices[1], 2)                          # into wave-1 territory
    if wave == 5 and len(prices) >= 5:
        return round(prices[4], 2)                          # below wave-4 termination
    return round(prices[0], 2)                              # wave 1 / default: the origin
