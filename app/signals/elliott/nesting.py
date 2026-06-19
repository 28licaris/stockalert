"""Wave nesting (V3-1) — validate a count against its subdivisions.

Elliott Wave is fractal: a motive wave subdivides into 5 waves one degree down,
a corrective into 3. A count is only as trustworthy as its sub-structure — a
"wave 2" that is a single bar can't be a real correction. This module scores how
well each wave of a candidate subdivides using the next finer degree's pivots,
and folds that into the candidate's confidence.

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
# "proportionate"; below it, the wave is suspiciously small (likely a noise pivot).
_PROP_FLOOR = 0.4


def proportionality_score(pivots: list[Pivot]) -> float:
    """1.0 when a count's waves are comparable in time-extent; lower when one
    wave is tiny relative to its siblings (a 1-bar 'wave 2' among 15-bar waves
    is almost always a noise pivot, not a real Elliott wave). Same-degree, so it
    bites even at the finest degree where there's nothing finer to subdivide."""
    spans = [pivots[i + 1].index - pivots[i].index for i in range(len(pivots) - 1)]
    if len(spans) < 2:
        return 1.0
    med = statistics.median(spans)
    if med <= 0:
        return 1.0
    ratio = min(spans) / med
    return round(min(1.0, ratio / _PROP_FLOOR), 3)


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


def apply_nesting(cand: WaveCandidate, by_degree: dict[int, list[Pivot]]) -> None:
    """Mutate `cand`: combine proportionality (same-degree) with finer-degree
    subdivision validation into `nesting_score`, and fold it into confidence.

    - Proportionate, cleanly-subdividing count → nesting ≈ 1.0 → confidence kept.
    - Disproportionate count (a 1-bar wave) or one whose waves can't form their
      expected sub-structure → discounted.
    Proportionate counts with no finer degree (e.g. single-degree synthetic data)
    keep nesting_score 1.0 — so v2 behaviour is preserved exactly there."""
    # Proportionality applies to EVERY structure (a 1-bar wave is noise whether
    # it's labeled an impulse leg or a zigzag leg). Finer-degree subdivision
    # validation is impulse-only for now (V3-5 adds corrective subdivisions).
    prop = proportionality_score(cand.pivots)
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
        nesting = 0.5 * prop + 0.5 * sub
    else:
        nesting = prop                            # finest degree: proportionality only

    cand.nesting_score = round(nesting, 3)
    cand.confidence = round(cand.confidence * (0.6 + 0.4 * nesting), 3)
