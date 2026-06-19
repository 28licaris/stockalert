"""WaveEngine — the deterministic, no-look-ahead Elliott labeler.

`label()` is a **pure function** of its inputs: confirmed pivots + the current
price + the as-of index. It enumerates candidate counts whose final (open) leg
ends at the latest confirmed swing and reaches the current price, discards any
that break a hard rule, scores the survivors by Fibonacci fit, and returns a
deterministically-ranked `WaveLabeling` (primary + secondary + alternates).

Determinism: ties break on the tuple of pivot bar-indices, never on a float
(spec D5). No-look-ahead: only pivots with `confirmed_at_index <= as_of_index`
are ever considered (spec D2); the engine therefore cannot revise a past label
when future bars arrive (`tests/test_elliott_no_lookahead.py`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from app.indicators.pivots import Pivot
from app.signals.elliott import fib, rules
from app.signals.elliott.schemas import WaveCandidate, WaveLabeling

_IMPULSE_LABELS = ["0", "1", "2", "3", "4", "5"]
_ZIGZAG_LABELS = ["0", "A", "B", "C"]


def alternate(pivots: list[Pivot]) -> list[Pivot]:
    """Reduce pivots (sorted by index) to a strictly alternating high/low swing
    sequence, keeping the most extreme of any same-kind run."""
    out: list[Pivot] = []
    for p in sorted(pivots, key=lambda q: q.index):
        if out and out[-1].kind == p.kind:
            prev = out[-1]
            keep = p if ((p.kind == "high" and p.price > prev.price)
                         or (p.kind == "low" and p.price < prev.price)) else prev
            out[-1] = keep
        else:
            out.append(p)
    return out


def _expected_sign(corr_or_trend_sign: int, wave_num: int) -> int:
    """With-trend legs (odd) move in the structure's sign; counter legs (even)
    move against it."""
    return corr_or_trend_sign if wave_num % 2 == 1 else -corr_or_trend_sign


def _leg_ok(last_price: float, last_pivot: float, exp_sign: int) -> bool:
    return (last_price - last_pivot) * exp_sign >= 0


def _structure_weight(m: int) -> float:
    """More confirmed swings → more confidence the structure is real."""
    return min(1.0, 0.40 + 0.13 * (m - 1))


def _room_factor(price: float, stop: float, floor: float = 0.012) -> float:
    """Penalise a *completed* structure whose stop sits almost on the current
    price — that means price has retraced back to the structure's origin, so the
    count is marginal/coincidental (e.g. a down-zigzag fully retraced). Full
    credit once the stop is at least `floor` (1.2%) away. NOT applied to
    in-progress trend waves, where a tight stop is a *good* entry, not a flaw."""
    if price <= 0:
        return 1.0
    return min(1.0, abs(price - stop) / price / floor)


# Mild structural prior: impulses are the tradeable trend structures; a
# completed correction is a weaker standalone read. Keeps an impulse ahead of a
# zigzag of otherwise-equal fit.
_ZIGZAG_PRIOR = 0.92


class WaveEngine:
    version = "ew2.0.0"

    def __init__(self, top_k: int = 3, min_confidence: float = 0.5,
                 secondary_floor: float = 0.15) -> None:
        self.top_k = top_k
        self.min_confidence = min_confidence
        self.secondary_floor = secondary_floor

    # -- public -------------------------------------------------------------
    def label(self, pivots: list[Pivot], last_price: float, *, symbol: str,
              interval: str, as_of_index: int, as_of: datetime) -> WaveLabeling:
        confirmed = [p for p in pivots if p.confirmed_at_index <= as_of_index]
        # Multi-degree synthesis: alternation must be clean *within* a degree, so
        # we group by degree and enumerate per degree, then pool the candidates
        # and let confidence rank across degrees. A clean impulse at any degree
        # surfaces — the modal-degree-only approach missed exactly those.
        by_degree: dict[int, list[Pivot]] = {}
        for p in confirmed:
            by_degree.setdefault(p.degree, []).append(p)

        cands: list[WaveCandidate] = []
        n_swings = 0
        for plist in by_degree.values():
            alt = alternate(plist)
            n_swings = max(n_swings, len(alt))
            L = len(alt)
            for start in range(max(0, L - 6), max(0, L - 2)):  # runs of length 3..6
                run = alt[start:]
                cands.extend(self._impulse(run, last_price))
                cands.extend(self._zigzag(run, last_price))

        cands = _dedupe(cands)
        cands.sort(key=lambda c: (-c.confidence, tuple(p.index for p in c.pivots)))

        primary = cands[0] if cands and cands[0].confidence >= self.min_confidence else None
        secondary = None
        alternates: list[WaveCandidate] = []
        if primary is not None:
            rest = [c for c in cands[1:] if c.confidence >= self.secondary_floor]
            secondary = rest[0] if rest else None
            alternates = rest[1:self.top_k]

        _normalize_probabilities([c for c in (primary, secondary) if c])
        surfaced = sum(c.probability for c in (primary, secondary) if c)

        return WaveLabeling(
            symbol=symbol, interval=interval, as_of=as_of, as_of_index=as_of_index,
            as_of_price=last_price, n_confirmed_swings=n_swings,
            primary=primary, secondary=secondary, alternates=alternates,
            current_wave=primary.current_wave if primary else None,
            confidence=primary.confidence if primary else 0.0,
            uncertainty=round(max(0.0, 1.0 - surfaced), 3),
            engine_ver=self.version,
        )

    # -- candidate builders -------------------------------------------------
    def _impulse(self, run: list[Pivot], last_price: float) -> list[WaveCandidate]:
        m = len(run)
        if m < 3 or m > 6:
            return []
        direction = "up" if run[0].kind == "low" else "down"
        s = 1 if direction == "up" else -1
        prices = [p.price for p in run]

        passed = rules.evaluate_impulse(prices, direction)
        if not passed or not all(passed.values()):
            return []

        ow = m if m <= 5 else 6  # open wave number; 6 == complete
        current = str(ow) if ow <= 5 else "complete"
        leg_ok = (ow > 5) or _leg_ok(last_price, prices[-1], _expected_sign(s, ow))

        fib_score = fib.score_impulse(prices, direction)
        conf = _structure_weight(m) * (0.45 + 0.55 * fib_score) * (1.0 if leg_ok else 0.4)
        wave_int = ow if ow <= 5 else 5
        targets = fib.impulse_targets(prices, direction, wave_int)
        invalid = fib.impulse_invalidation(prices, direction, wave_int)

        # Reject counts the current price has ALREADY invalidated: for an
        # up-impulse price must sit above the stop, for a down-impulse below it.
        # (price - invalid) * s must be > 0. A wave-4-down at new highs is dead.
        if current != "complete" and (last_price - invalid) * s <= 0:
            return []
        if current == "complete":
            conf *= _room_factor(last_price, invalid)

        rat = _impulse_rationale(direction, current, prices, invalid, targets)
        return [WaveCandidate(
            structure="impulse", direction=direction, current_wave=current,
            degree=run[0].degree, pivots=run, labels=_IMPULSE_LABELS[:m],
            rules_passed=passed, rule_score=1.0, fib_score=fib_score,
            confidence=round(conf, 3), invalidation_price=invalid,
            fib_targets=targets, rationale=rat,
        )]

    def _zigzag(self, run: list[Pivot], last_price: float) -> list[WaveCandidate]:
        m = len(run)
        if m < 3 or m > 4:
            return []
        # correction direction = direction of leg A (origin → first swing)
        direction = "up" if run[0].kind == "low" else "down"
        s = 1 if direction == "up" else -1
        prices = [p.price for p in run]

        passed = rules.evaluate_zigzag(prices, direction)
        if not passed or not all(passed.values()):
            return []

        # m swings, run anchored to end at the latest swing:
        #   m=3 → [origin, A-end, B-end] confirmed, open leg → wave C
        #   m=4 → [origin, A, B, C] all confirmed → complete
        letter = {3: "C", 4: "complete"}.get(m, "C")
        wave_idx = {"A": 1, "B": 2, "C": 3}.get(letter, 3)
        leg_ok = (letter == "complete") or _leg_ok(last_price, prices[-1], _expected_sign(s, wave_idx))

        fib_score = fib.score_zigzag(prices, direction)
        conf = _structure_weight(m) * (0.40 + 0.55 * fib_score) * (1.0 if leg_ok else 0.4) * _ZIGZAG_PRIOR

        a = abs(prices[1] - prices[0])
        targets: dict[str, float] = {}
        invalid = round(prices[0], 2)
        if letter == "C" and a:
            anchor = prices[2]
            targets = {"C=1.0xA": round(anchor + s * a, 2),
                       "C=1.618xA": round(anchor + s * 1.618 * a, 2)}
            invalid = round(prices[2], 2)  # stop beyond wave-B termination

        # Reject if current price already breached the stop (a "complete" down
        # zigzag with price back above its origin is no longer a valid read).
        if (last_price - invalid) * s <= 0:
            return []
        if letter == "complete":
            conf *= _room_factor(last_price, invalid)

        art = "an" if direction == "up" else "a"
        if letter == "complete":
            rat = (f"{art.capitalize()} {direction} zigzag (A-B-C) appears complete — "
                   f"the correction may be ending. Stop {invalid}.")
        else:
            rat = (f"In wave {letter} of {art} {direction} zigzag (A-B-C correction). "
                   f"Stop {invalid}." + (f" Target {next(iter(targets.values()))}." if targets else ""))
        return [WaveCandidate(
            structure="zigzag", direction=direction, current_wave=letter,
            degree=run[0].degree, pivots=run, labels=_ZIGZAG_LABELS[:m],
            rules_passed=passed, rule_score=1.0, fib_score=fib_score,
            confidence=round(conf, 3), invalidation_price=invalid,
            fib_targets=targets, rationale=rat,
        )]


# -- helpers ----------------------------------------------------------------
def _dedupe(cands: list[WaveCandidate]) -> list[WaveCandidate]:
    best: dict[tuple, WaveCandidate] = {}
    for c in cands:
        key = (c.structure, c.direction, c.current_wave, c.degree,
               tuple(p.index for p in c.pivots))
        if key not in best or c.confidence > best[key].confidence:
            best[key] = c
    return list(best.values())


def _normalize_probabilities(counts: list[WaveCandidate]) -> None:
    """Keep raw confidences as probabilities but always leave some uncertainty
    mass: if they sum past 0.9, scale down proportionally."""
    s = sum(c.confidence for c in counts)
    for c in counts:
        c.probability = round(c.confidence * (0.9 / s), 3) if s > 0.9 else round(c.confidence, 3)


def _impulse_rationale(direction: str, current: str, prices: list[float],
                       invalid: float, targets: dict[str, float]) -> str:
    art = "an" if direction == "up" else "a"
    if current == "complete":
        return (f"Five-wave {direction} impulse appears complete — expect an "
                f"A-B-C correction. Structural stop {invalid}.")
    bits = [f"In wave {current} of {art} {direction} impulse."]
    if current == "3" and len(prices) >= 3 and (prices[1] - prices[0]):
        w2r = abs(prices[1] - prices[2]) / abs(prices[1] - prices[0])
        bits.append(f"Wave 2 retraced {w2r:.0%} of wave 1 (ideal .618).")
    bits.append(f"Stop {invalid}.")
    if targets:
        bits.append(f"Target {next(iter(targets.values()))}.")
    return " ".join(bits)
