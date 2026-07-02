"""
Significance machinery for strategy research: MCPT p-values and
multiple-hypothesis control.

The MCPT p-value answers "what is the probability that a result at
least this good arises from data with the same return distribution but
no temporal structure?" — computed against the null distribution
produced by re-running the FULL research procedure (including any
optimization) on permuted bars from `permutation.py`.

Multiple-hypothesis control: every grid sweep / signal family we test
is a family of hypotheses; the best survivor of many searches is
expected to look good by chance. `benjamini_hochberg` converts a
family of MCPT p-values into FDR-controlled q-values. Promotion rule
(docs/standards/trading_subsystem.md): a strategy needs walk-forward
MCPT q <= 0.05 within its pre-registered family before paper trading.
"""
from __future__ import annotations

from typing import Sequence

from pydantic import BaseModel, Field


class McptResult(BaseModel):
    """Outcome of one Monte Carlo permutation test."""

    real: float = Field(description="Metric on the real data")
    n_permutations: int
    n_as_good: int = Field(description="Permutations with metric >= real (or <= if lower is better)")
    p_value: float = Field(description="(1 + n_as_good) / (1 + n_permutations)")
    perm_mean: float
    perm_std: float

    def summary(self) -> str:
        return (
            f"real={self.real:.4f} vs null mean={self.perm_mean:.4f} "
            f"sd={self.perm_std:.4f} over {self.n_permutations} permutations "
            f"-> p={self.p_value:.4f} ({self.n_as_good} as good)"
        )


def mcpt_pvalue(
    real: float,
    permuted: Sequence[float],
    *,
    greater_is_better: bool = True,
) -> McptResult:
    """
    Empirical permutation p-value with the add-one convention (the real
    run counts as one member of the null ensemble), so p is never 0 and
    the minimum resolvable p is 1/(n+1).
    """
    vals = [float(v) for v in permuted]
    if not vals:
        raise ValueError("mcpt_pvalue: empty permutation ensemble")
    if greater_is_better:
        n_as_good = sum(1 for v in vals if v >= real)
    else:
        n_as_good = sum(1 for v in vals if v <= real)
    n = len(vals)
    mean = sum(vals) / n
    var = sum((v - mean) ** 2 for v in vals) / n
    return McptResult(
        real=float(real),
        n_permutations=n,
        n_as_good=n_as_good,
        p_value=(1 + n_as_good) / (1 + n),
        perm_mean=mean,
        perm_std=var ** 0.5,
    )


def benjamini_hochberg(p_values: Sequence[float]) -> list[float]:
    """
    BH step-up FDR adjustment. Returns q-values in the input order:
    q_(i) = min_{j >= i} ( p_(j) * m / j ), capped at 1.

    Reject hypothesis i at FDR level alpha iff q_i <= alpha.
    """
    m = len(p_values)
    if m == 0:
        raise ValueError("benjamini_hochberg: no p-values supplied")
    for p in p_values:
        if not (0.0 <= p <= 1.0):
            raise ValueError(f"benjamini_hochberg: p-value {p} outside [0, 1]")
    order = sorted(range(m), key=lambda i: p_values[i])
    q = [0.0] * m
    running_min = 1.0
    for rank_from_top in range(m, 0, -1):
        i = order[rank_from_top - 1]
        running_min = min(running_min, p_values[i] * m / rank_from_top)
        q[i] = running_min
    return q
