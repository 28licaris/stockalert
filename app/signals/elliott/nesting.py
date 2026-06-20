"""Wave nesting (V3-1/V3-6) — validate a count against its subdivisions.

Elliott Wave is fractal: a motive wave subdivides into 5 waves one degree down,
a corrective into 3. A count is only as trustworthy as its sub-structure — a
"wave 2" that is a single bar can't be a real correction. This module scores how
well each wave of a candidate subdivides using the next finer degree's pivots,
and folds that into the candidate's confidence.

V3-6 (degree coherence): `degree_coherence_score` penalises counts whose pivots
are anchored far back in history and whose wave sizes are outsized relative to
the current price. A professional picks the finer degree that fills the recent
chart meaningfully; we now reward that behaviour automatically.

Pure + deterministic + causal: it only ever uses pivots already confirmed at the
as-of bar (the caller passes the same per-degree streams the engine labels on),
so nesting cannot leak future information.
"""
from __future__ import annotations

import statistics

from app.indicators.pivots import Pivot
from app.signals.elliott import fib, rules
from app.signals.elliott.schemas import WaveCandidate

# A wave with no detectable finer structure can't be validated either way —
# give it a mild neutral score rather than full credit or a hard zero.
_UNASSESSABLE = 0.4
# A wave whose bar-span is at least this fraction of the median wave span is
# "proportionate"; below it, the wave is suspiciously small.
_PROP_FLOOR = 0.4
# Below this ratio the wave is almost certain noise (e.g. 1 bar among 9-bar waves
# = 0.11). The multiplicative nesting formula turns this near-zero score into
# a confidence that falls below min_confidence, eliminating the candidate.
_PROP_DEAD_FLOOR = 0.15


def proportionality_score(pivots: list[Pivot]) -> float:
    """1.0 when a count's waves are comparable in time-extent.

    Three zones:
      ratio ≥ _PROP_FLOOR (0.40) → 1.0 (fully proportionate)
      _PROP_DEAD_FLOOR ≤ ratio < _PROP_FLOOR → linear 0.10 → 1.0
      ratio < _PROP_DEAD_FLOOR (0.15)  → near-zero (<0.10) — noise

    The near-zero zone matters most: a 1-bar wave among 9-bar waves has ratio
    ≈ 0.11, which via the multiplicative `apply_nesting` formula drives the
    final confidence below `min_confidence` and eliminates the count.
    """
    spans = [pivots[i + 1].index - pivots[i].index for i in range(len(pivots) - 1)]
    if len(spans) < 2:
        return 1.0
    med = statistics.median(spans)
    if med <= 0:
        return 1.0
    ratio = min(spans) / med
    if ratio >= _PROP_FLOOR:
        return 1.0
    if ratio < _PROP_DEAD_FLOOR:
        # Near-zero: severe noise — returns < 0.10, which zeroes out nesting
        return round(ratio / _PROP_DEAD_FLOOR * 0.10, 3)
    # Linear interpolation: [_PROP_DEAD_FLOOR, _PROP_FLOOR) → [0.10, 1.0)
    t = (ratio - _PROP_DEAD_FLOOR) / (_PROP_FLOOR - _PROP_DEAD_FLOOR)
    return round(0.10 + 0.90 * t, 3)


def degree_coherence_score(cand: WaveCandidate, last_price: float,
                           as_of_index: int) -> float:
    """Score how actionable the candidate's degree is given the current bar context.

    A count anchored at the very start of the lookback window (oldest pivot near
    bar 0) with large individual wave sizes relative to current price is
    historically valid but not immediately tradeable — a professional works the
    finer degree whose pivots are all recent. This score nudges the engine to
    prefer that finer count when both candidates pass the hard EW rules.

    Returns 1.0 (neutral) when last_price or as_of_index are zero — preserves
    existing behaviour for tests that don't supply price context.

    Recency (0.65 weight): oldest_pivot_idx / as_of_index — 0 = ancient, 1 = now.
    Size   (0.35 weight): max single-wave price move as a fraction of last_price.
      Full credit ≤ 30 %; linear decay to 0 above 50 %.
    """
    if not cand.pivots or as_of_index <= 0 or last_price <= 0:
        return 1.0

    oldest_idx = min(p.index for p in cand.pivots)
    recency = oldest_idx / as_of_index  # 0.0 = very old, 1.0 = at most-recent bar

    max_span_pct = max(
        abs(cand.pivots[i + 1].price - cand.pivots[i].price) / last_price
        for i in range(len(cand.pivots) - 1)
    )
    # Full credit ≤ 30 %; linear decay to 0 at ≥ 50 %
    size_score = max(0.0, 1.0 - max(0.0, max_span_pct - 0.30) / 0.20)

    return round(0.65 * recency + 0.35 * size_score, 3)


def _alternate(pivots: list[Pivot]) -> list[Pivot]:
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


def _subdivide_wave(start: Pivot, end: Pivot, finer_alt: list[Pivot],
                    direction: str, motive: bool) -> tuple[float, dict]:
    """Score how well one wave [start,end] subdivides into the expected count
    (5 if motive, 3 if corrective) using the finer-degree pivots inside it."""
    inner = _alternate([p for p in finer_alt if start.index < p.index < end.index])
    expected_inner = 4 if motive else 2
    info = {"structure": "impulse" if motive else "corrective",
            "direction": direction, "n_subpivots": len(inner)}
    if not inner:
        info["score"] = _UNASSESSABLE
        return _UNASSESSABLE, info

    prices = [p.price for p in ([start] + inner[:expected_inner] + [end])]
    passed = (rules.evaluate_impulse(prices, direction) if motive
              else rules.evaluate_zigzag(prices, direction))
    completeness = min(1.0, len(inner) / expected_inner)
    if passed and all(passed.values()):
        fit = fib.score_impulse(prices, direction) if motive else fib.score_zigzag(prices, direction)
        score = round(0.45 + 0.30 * completeness + 0.25 * fit, 3)
    else:
        # sub-structure exists but breaks a rule in the expected direction — weak.
        score = round(0.2 + 0.1 * completeness, 3)
    info["score"] = score
    return score, info


def apply_nesting(cand: WaveCandidate, by_degree: dict[int, list[Pivot]],
                  last_price: float = 0.0, as_of_index: int = 0) -> None:
    """Mutate `cand`: combine proportionality, finer-degree subdivision
    validation, and degree coherence (V3-6) into `nesting_score`, then fold
    into confidence.

    Weights:
      - impulse with finer degree: 0.35 prop + 0.35 sub + 0.30 coherence
      - all other cases           : 0.60 prop + 0.40 coherence

    Backward-compatible: when called without last_price/as_of_index (both
    default to 0), degree_coherence_score returns 1.0 (neutral) so all
    existing test contracts are preserved exactly.
    """
    prop = proportionality_score(cand.pivots)
    coh = degree_coherence_score(cand, last_price, as_of_index)
    finer = by_degree.get(cand.degree - 1) if cand.degree > 0 else None

    if cand.structure == "impulse" and finer:
        finer_alt = _alternate(finer)
        opp = "down" if cand.direction == "up" else "up"
        scores: list[float] = []
        subwaves: list[dict] = []
        piv = cand.pivots
        for k in range(1, len(piv)):             # wave k spans piv[k-1] → piv[k]
            motive = (k % 2 == 1)                # waves 1,3,5 motive; 2,4 corrective
            wdir = cand.direction if motive else opp
            sc, info = _subdivide_wave(piv[k - 1], piv[k], finer_alt, wdir, motive)
            info["wave"] = cand.labels[k] if k < len(cand.labels) else str(k)
            scores.append(sc)
            subwaves.append(info)
        sub = (sum(scores) / len(scores)) if scores else 1.0
        cand.subwaves = subwaves
        # Multiplicative: prop gates sub+coh. A disproportionate count (1-bar wave)
        # can't be rescued by high coherence or clean subdivisions.
        nesting = prop * (0.50 * sub + 0.50 * coh)
    else:
        nesting = prop * (0.60 + 0.40 * coh)

    cand.nesting_score = round(nesting, 3)
    cand.confidence = round(cand.confidence * (0.6 + 0.4 * nesting), 3)
