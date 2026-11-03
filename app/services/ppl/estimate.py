"""Fit the conditional drift, the volatility path, and the tail index.

All pure: arrays in, dataclasses out. Nothing here touches IO or config.

The three estimators, and why each is shaped the way it is:

* **Drift** is estimated per conditioning state and shrunk *hard* toward zero.
  Intraday drift is tiny and trivially overfit; with 32 states over a few
  thousand bars, an unshrunk per-state mean is mostly noise. The consequence is
  deliberate and recorded in the spec: our fans come out close to symmetric.

* **Volatility** is an EWMA of squared log returns scaled by a time-of-day
  factor. The factor is estimated per *session slot*, which means it absorbs
  both the intraday U-shape (open and close are more volatile than midday) and
  the overnight gap — slot 0's return spans the gap, so its slot factor is
  naturally large. One mechanism, no separate overnight term.

* **Tail index** ν is moment-matched from the excess kurtosis of standardized
  residuals and floored at 3.5, because Student-t variance is undefined at
  ν ≤ 2 and wildly unstable just above it.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Excess kurtosis below this is treated as "no fat tails to speak of" and maps
# to the ν ceiling rather than to a divide-by-a-hair.
_MIN_EXCESS_KURTOSIS = 1e-6
NU_FLOOR = 3.5
NU_CEILING = 30.0


@dataclass(frozen=True)
class DriftModel:
    """Shrunk per-state mean log return, with the support behind each estimate."""

    by_state: dict[int, float]
    counts: dict[int, int]
    unconditional: float
    kappa: float
    min_state_obs: int

    def mu(self, state: int) -> tuple[float, int, bool]:
        """Return `(drift, n_observations, pooled)` for a state.

        States with thin support fall back to the unconditional mean and say so
        via `pooled` — they are never silently binned into someone else's
        estimate or dropped to zero without a trace.
        """
        n = self.counts.get(state, 0)
        if n < self.min_state_obs:
            return self.unconditional, n, True
        return self.by_state[state], n, False


@dataclass(frozen=True)
class VolModel:
    """Latest EWMA sigma plus the per-slot time-of-day multipliers."""

    sigma: float
    tod_factors: np.ndarray      # length == bars_per_session, mean 1.0
    bars_per_session: int

    def path(self, start_slot: int, horizon: int) -> np.ndarray:
        """Per-step sigma for `horizon` bars starting at the slot *after*
        `start_slot` — i.e. the sigma of the return from the anchor forward."""
        if self.bars_per_session <= 1:
            return np.full(horizon, self.sigma, dtype="float64")
        slots = (start_slot + 1 + np.arange(horizon)) % self.bars_per_session
        return self.sigma * self.tod_factors[slots]


def fit_drift(
    states: np.ndarray,
    forward_log_returns: np.ndarray,
    *,
    kappa: float,
    min_state_obs: int,
) -> DriftModel:
    """Per-state mean forward log return, shrunk toward zero.

        mu(s) = mean(s) * n_s / (n_s + kappa)

    `states[i]` must be the state at bar `i` and `forward_log_returns[i]` the
    return from bar `i` to `i+1`. Aligning those is the caller's job — getting
    it wrong is a look-ahead bug, so it is done once, in `service.py`.
    """
    if states.shape != forward_log_returns.shape:
        raise ValueError(
            f"states {states.shape} and returns {forward_log_returns.shape} must align"
        )

    usable = (states >= 0) & np.isfinite(forward_log_returns)
    states, forward_log_returns = states[usable], forward_log_returns[usable]
    if states.size == 0:
        return DriftModel({}, {}, 0.0, kappa, min_state_obs)

    unconditional = float(forward_log_returns.mean())

    by_state: dict[int, float] = {}
    counts: dict[int, int] = {}
    for s in np.unique(states):
        sel = forward_log_returns[states == s]
        n = int(sel.size)
        counts[int(s)] = n
        by_state[int(s)] = float(sel.mean()) * n / (n + kappa)

    return DriftModel(
        by_state=by_state,
        counts=counts,
        unconditional=unconditional * len(forward_log_returns) / (len(forward_log_returns) + kappa),
        kappa=kappa,
        min_state_obs=min_state_obs,
    )


def fit_ewma_sigma(log_returns: np.ndarray, lam: float) -> float:
    """Latest EWMA volatility: `var_t = lam*var_{t-1} + (1-lam)*r_{t-1}^2`.

    Seeded with the sample variance so the recursion starts calibrated rather
    than climbing out of zero for the first few hundred bars.
    """
    r = log_returns[np.isfinite(log_returns)]
    if r.size < 2:
        raise ValueError(f"need at least 2 finite returns to fit EWMA sigma, got {r.size}")

    var = float(np.var(r))
    for x in r:
        var = lam * var + (1.0 - lam) * float(x) ** 2
    return float(np.sqrt(var))


def fit_tod_factors(
    log_returns: np.ndarray, slots: np.ndarray, bars_per_session: int
) -> np.ndarray:
    """Per-slot volatility multipliers, normalized to mean 1.

    Slots with too little data to estimate fall back to 1.0 rather than to a
    noisy one-off standard deviation.
    """
    if bars_per_session <= 1:
        return np.ones(1, dtype="float64")

    factors = np.ones(bars_per_session, dtype="float64")
    finite = np.isfinite(log_returns)
    for s in range(bars_per_session):
        sel = log_returns[finite & (slots == s)]
        if sel.size >= 20:
            factors[s] = float(np.std(sel))

    positive = factors[factors > 0]
    if positive.size == 0:
        return np.ones(bars_per_session, dtype="float64")

    factors[factors <= 0] = float(positive.mean())
    return factors / float(factors.mean())


def fit_nu(standardized_residuals: np.ndarray) -> float:
    """Moment-match Student-t degrees of freedom from excess kurtosis.

    For a Student-t, excess kurtosis `k = 6 / (ν - 4)` when ν > 4, giving
    `ν = 4 + 6/k`. Clamped to `[NU_FLOOR, NU_CEILING]`: below the floor the
    variance is unstable (undefined at ν ≤ 2), above the ceiling the
    distribution is Gaussian for our purposes.
    """
    r = standardized_residuals[np.isfinite(standardized_residuals)]
    if r.size < 30:
        return 4.0

    centered = r - r.mean()
    sd = float(np.std(centered))
    if sd <= 0:
        return NU_CEILING

    excess = float(np.mean((centered / sd) ** 4)) - 3.0
    if excess <= _MIN_EXCESS_KURTOSIS:
        return NU_CEILING

    return float(np.clip(4.0 + 6.0 / excess, NU_FLOOR, NU_CEILING))


def standardize(log_returns: np.ndarray, sigma_by_bar: np.ndarray) -> np.ndarray:
    """Residuals scaled to unit variance, for the ν fit."""
    with np.errstate(divide="ignore", invalid="ignore"):
        out = np.where(sigma_by_bar > 0, log_returns / sigma_by_bar, np.nan)
    return out
