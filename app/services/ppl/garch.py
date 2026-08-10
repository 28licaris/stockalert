"""GJR-GARCH(1,1) volatility dynamics + Filtered Historical Simulation.

This module exists because the EWMA model it replaces failed PIT calibration,
and measurement said exactly why. Three properties of equity returns, all
measured on SPY daily over the fit window:

| Property | Measured | EWMA + iid-t assumes |
|---|---|---|
| Volatility clustering | `corr(|r_t|, |r_{t-1}|) = +0.32` | captured (EWMA does cluster) |
| Leverage effect | RMS after a down day is **1.28x** after an up day | symmetric — no distinction |
| Variance mean-reversion | `var(H) / (H * var(1)) = 0.715` at H=40 | 1.0 — variance scales linearly |

The last two are the failures. EWMA is IGARCH: its persistence is exactly 1, so
a shock never decays and the fan grows as `sigma*sqrt(H)` forever. At H=40 that
made it ~18% too wide. And having no leverage term, it produced symmetric fans
for an asset whose realized 40-bar skew is -1.68.

**Why not just skew the innovations.** That was the first hypothesis and it is
wrong. If `eps_t` is iid with skew `g`, the skew of a sum of H of them decays as
`g/sqrt(H)`: at `g = -0.44` and H=40 that predicts -0.069. SPY's realized H=40
skew is -1.68 — 24x larger, and it *grows* with H where the iid prediction
shrinks. Horizon skew is not a property of the marginal distribution; it is
produced by the **path dynamics**, where a negative draw raises the volatility
of every subsequent draw. Only a leverage term can generate it.

## The model

    eps_t     = sigma_t * z_t                          z_t ~ (pool), mean 0, var 1
    sigma^2_t = omega + (alpha + gamma*1[eps_{t-1}<0]) * eps^2_{t-1}
                      + beta * sigma^2_{t-1}

Glosten, Jagannathan & Runkle (1993). `gamma > 0` is the leverage effect: a
negative shock of the same magnitude raises tomorrow's variance by
`(alpha+gamma)/alpha` times as much as a positive one.

Persistence is `alpha + gamma/2 + beta` (the `/2` because the indicator fires on
half of a symmetric innovation distribution). Stationarity requires it `< 1`,
and that inequality is precisely what makes variance mean-revert — the thing
EWMA structurally cannot do.

## Why the innovations are bootstrapped, not parametric

Filtered Historical Simulation — Barone-Adesi, Bourgoin & Giannopoulos (1998).
Once GARCH has filtered out the volatility dynamics, the standardized residuals
`z_t = eps_t / sigma_t` are approximately iid. Their **empirical** distribution
is then the honest innovation model: whatever skew, kurtosis and tail shape the
asset actually has, with no parametric family imposed on top.

This also removes a free parameter. The old model fit a Student-t `nu` by
moment-matching; the pool carries the same information without the assumption
that the true shape is a Student-t at all.

`residual_abs_autocorr` is the check that the filter worked: if GJR-GARCH
captured the clustering, `corr(|z_t|,|z_{t-1}|)` should collapse toward zero
from the raw series' `raw_abs_autocorr`. It is reported on every fit so the
premise is falsifiable rather than assumed.

## Estimation

Gaussian quasi-MLE, via `arch` (Sheppard). Gaussian and not Student-t on
purpose: QMLE is consistent for the GARCH parameters even when the true
innovations are non-Gaussian (Bollerslev & Wooldridge 1992), which is the right
robustness posture when the whole point is that we are about to bootstrap the
residuals rather than trust a shape.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np

logger = logging.getLogger(__name__)

# Log returns are ~1e-2, and an optimizer working a likelihood in those units
# hits its gradient tolerance long before the parameters have converged. The
# series is therefore rescaled to a target standard deviation before fitting.
#
# This is exact, not an approximation: alpha, beta, gamma and the standardized
# residuals are scale-free, and omega scales as scale^2, so unscaling afterwards
# recovers the parameters of the original series precisely. Scaling adaptively
# rather than by a fixed 100 keeps a low-volatility symbol (or a daily series,
# which has ~1/5 the per-bar variance of a 30m one) inside the optimizer's
# comfortable range instead of only the typical case.
_TARGET_FIT_SD = 10.0

# Below this the GARCH parameters are fit on noise. 500 daily bars is ~2 years.
MIN_GARCH_OBS = 500

# `arch` constrains the optimizer to alpha + gamma/2 + beta < 1, so a fit that
# comes back AT this bound has not found an interior optimum — the constrained
# MLE terminated on the boundary. That is an estimation failure, not a finding
# that the asset is IGARCH, and a boundary solution is the standard thing to
# reject.
#
# It is also usually diagnostic of something specific: a fit window straddling a
# volatility regime change is pushed toward spurious IGARCH (Lamoureux &
# Lastrapes 1990). Empirically these cluster in exactly those windows — TLT 1h
# fails only across 2008-09, where its annualized vol went 8.9% -> 16.3% -> 18.1%.
MAX_PERSISTENCE = 0.99999


class GarchFitError(RuntimeError):
    """The GJR-GARCH fit did not produce a usable model.

    Raised rather than absorbed: a silent fall back to a weaker volatility model
    would put an uncalibrated fan on screen wearing a calibrated model's name.
    """


@dataclass(frozen=True)
class GjrGarchFit:
    """A fitted GJR-GARCH(1,1) plus everything Filtered Historical Simulation
    needs to roll it forward.

    All fields are in the units of the returns handed to `fit_gjr_garch` — the
    percent rescaling used internally is undone here.
    """

    omega: float
    alpha: float
    gamma: float
    beta: float

    # One-step-ahead conditional sd for the FIRST simulated bar. Already
    # incorporates the sign of the last observed innovation, which is the whole
    # point of the leverage term: a fan built the evening of a -3% day starts
    # wider than one built after +3%.
    sigma_next: float

    # Standardized residuals, demeaned and rescaled to exactly unit variance.
    # Demeaned because drift is owned solely by `DriftModel` — leaving the
    # residual mean in would add the risk premium twice.
    residual_pool: np.ndarray = field(repr=False)

    n_obs: int
    loglikelihood: float

    # Diagnostics. Reported, not asserted — they are how the model's premises
    # stay checkable from a snapshot.
    raw_abs_autocorr: float          # clustering in |r| BEFORE filtering
    residual_abs_autocorr: float     # ... and after. Should collapse toward 0.
    residual_skew: float
    residual_kurtosis: float         # excess

    @property
    def persistence(self) -> float:
        """`alpha + gamma/2 + beta`. Must be < 1 for variance to mean-revert."""
        return self.alpha + self.gamma / 2.0 + self.beta

    @property
    def long_run_variance(self) -> float:
        """`omega / (1 - persistence)` — the level variance decays back toward."""
        return self.omega / (1.0 - self.persistence)

    @property
    def long_run_sigma(self) -> float:
        return float(np.sqrt(self.long_run_variance))

    @property
    def half_life_bars(self) -> float:
        """Bars for a variance shock to decay halfway to the long-run level."""
        p = self.persistence
        if p <= 0.0 or p >= 1.0:
            return float("inf")
        return float(np.log(0.5) / np.log(p))

    @property
    def leverage_ratio(self) -> float:
        """How much more a down shock moves variance than an up shock of equal
        size: `(alpha + gamma) / alpha`. 1.0 means no leverage effect.

        This is the textbook quantity, and it is **numerically unstable when
        `alpha` is near zero** — which is the common case for equity indices,
        where the fit puts essentially all news impact in the asymmetric term.
        SPY daily typically fits `alpha ~ 0.003, gamma ~ 0.26`, giving a ratio
        above 80 that is dominated by the denominator's estimation noise.

        Use it as a *sign and rough magnitude* indicator, not a precise figure.
        `vol_response_asymmetry` is the bounded alternative for display.
        """
        if self.alpha <= 0.0:
            return float("inf") if self.gamma > 0.0 else 1.0
        return (self.alpha + self.gamma) / self.alpha

    @property
    def vol_response_asymmetry(self) -> float:
        """Share of the average news impact that comes from down moves:
        `(alpha + gamma) / (2*alpha + gamma)`.

        Bounded in `[0, 1]` and stable as `alpha -> 0`, unlike `leverage_ratio`.
        0.5 is perfect symmetry; above 0.5 is the leverage effect; below 0.5
        means up moves drive volatility harder — which is real for gold, and a
        useful check that the model is measuring rather than assuming.
        """
        denom = 2.0 * self.alpha + self.gamma
        if denom <= 0.0:
            return 0.5
        return (self.alpha + self.gamma) / denom

    def variance_ratio(self, horizon: int) -> float:
        """`var(H) / (H * var(1))` under this model, ignoring the pool.

        The number EWMA gets structurally wrong. The k-step-ahead variance
        forecast decays geometrically toward the long-run level,

            E[sigma^2_{t+k}] = lr + p^k * (sigma^2_next - lr)

        and the H-step variance is their sum, which has a closed form:

            var(H) = H*lr + (sigma^2_next - lr) * (1 - p^H) / (1 - p)

        So the ratio comes in **below 1** when today's variance sits above its
        long-run level and **above 1** when below. EWMA is pinned at exactly 1.0
        in both directions — that is the defect, stated as one number.

        Note the ratio approaches its limit as O(1/H), not geometrically: the
        transient is a fixed quantity spread over a growing average.
        """
        if horizon < 1:
            raise ValueError(f"horizon must be >= 1, got {horizon}")

        p, lr = self.persistence, self.long_run_variance
        v0 = self.sigma_next**2

        # p is constrained below MAX_PERSISTENCE at fit time, so 1-p is bounded
        # away from zero; the guard is for hand-built fits in tests.
        gap = 1.0 - p
        decay = horizon if gap < 1e-12 else (1.0 - p**horizon) / gap

        total = horizon * lr + (v0 - lr) * decay
        return float(total / (horizon * v0))


def fit_gjr_garch(returns: np.ndarray) -> GjrGarchFit:
    """Fit GJR-GARCH(1,1) by Gaussian QMLE and package it for simulation.

    `returns` must be **mean-relevant but deseasonalized** log returns: at
    intraday intervals the caller divides by the time-of-day factor first
    (Andersen & Bollerslev 1997). Feeding the raw intraday series in would let
    the optimizer read the deterministic U-shape as volatility clustering and
    inflate `beta`.

    Raises `GarchFitError` on too little data, non-convergence, or a
    non-stationary fit. There is no fallback by design.
    """
    r = np.asarray(returns, dtype="float64")
    r = r[np.isfinite(r)]

    if r.size < MIN_GARCH_OBS:
        raise GarchFitError(
            f"GJR-GARCH needs at least {MIN_GARCH_OBS} finite returns, got {r.size}"
        )
    if not np.any(r != 0.0):
        raise GarchFitError("all returns are zero — no variance to model")

    try:
        from arch import arch_model
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise GarchFitError(
            "the `arch` package is required for the PPL volatility model; "
            "install it with `poetry install`"
        ) from exc

    sd = float(np.std(r))
    if not np.isfinite(sd) or sd <= 0.0:
        raise GarchFitError(f"returns have no usable spread (sd={sd})")
    scale = _TARGET_FIT_SD / sd

    # mean="Zero": drift is DriftModel's job and is added back in simulation.
    # Letting arch fit a constant mean here would double-count it.
    model = arch_model(
        r * scale, mean="Zero", vol="GARCH", p=1, o=1, q=1,
        dist="normal", rescale=False,
    )

    try:
        res = model.fit(disp="off", show_warning=False)
    except Exception as exc:  # arch raises a variety of numeric errors
        raise GarchFitError(f"GJR-GARCH optimizer failed: {exc}") from exc

    if res.convergence_flag != 0:
        raise GarchFitError(
            f"GJR-GARCH did not converge (scipy flag {res.convergence_flag}: "
            f"{res.optimization_result.message})"
        )

    p = res.params
    omega = float(p["omega"]) / scale**2
    alpha, gamma, beta = float(p["alpha[1]"]), float(p["gamma[1]"]), float(p["beta[1]"])

    persistence = alpha + gamma / 2.0 + beta
    if not np.isfinite(persistence) or persistence >= MAX_PERSISTENCE:
        raise GarchFitError(
            f"GJR-GARCH optimizer terminated on the stationarity boundary "
            f"(persistence {persistence:.6f} >= {MAX_PERSISTENCE}) — no interior "
            f"MLE over this window, so there is no long-run variance to revert "
            f"to. Usually a volatility regime change inside the fit window."
        )
    if omega <= 0.0:
        raise GarchFitError(f"GJR-GARCH fit returned non-positive omega ({omega:.3e})")

    # One-step-ahead conditional variance: roll the recursion one bar past the
    # last observation, using the last observed innovation and its SIGN.
    sigma_fit = np.asarray(res.conditional_volatility, dtype="float64") / scale
    last_eps = float(r[-1])
    sigma2_next = (
        omega
        + (alpha + (gamma if last_eps < 0.0 else 0.0)) * last_eps**2
        + beta * float(sigma_fit[-1]) ** 2
    )
    if not np.isfinite(sigma2_next) or sigma2_next <= 0.0:
        raise GarchFitError(f"one-step-ahead variance is not usable ({sigma2_next})")

    pool = _build_residual_pool(np.asarray(res.std_resid, dtype="float64"))

    fit = GjrGarchFit(
        omega=omega,
        alpha=alpha,
        gamma=gamma,
        beta=beta,
        sigma_next=float(np.sqrt(sigma2_next)),
        residual_pool=pool,
        n_obs=int(r.size),
        loglikelihood=float(res.loglikelihood),
        raw_abs_autocorr=abs_autocorr(r),
        residual_abs_autocorr=abs_autocorr(pool),
        residual_skew=_skew(pool),
        residual_kurtosis=_excess_kurtosis(pool),
    )

    logger.debug(
        "ppl.garch: n=%d omega=%.3e alpha=%.4f gamma=%.4f beta=%.4f "
        "persistence=%.4f half_life=%.1f leverage=%.2fx |r|-acf %.3f -> %.3f",
        fit.n_obs, fit.omega, fit.alpha, fit.gamma, fit.beta,
        fit.persistence, fit.half_life_bars, fit.leverage_ratio,
        fit.raw_abs_autocorr, fit.residual_abs_autocorr,
    )
    return fit


def _build_residual_pool(std_resid: np.ndarray) -> np.ndarray:
    """The FHS bootstrap pool: standardized residuals, mean 0, variance 1.

    Demeaning matters. `arch`'s zero-mean model leaves the sample's average
    return inside the standardized residuals, so bootstrapping them raw *and*
    adding `DriftModel`'s drift would apply the risk premium twice. Drift is
    owned in exactly one place.

    Rescaling to exactly unit variance matters less but is free: it removes a
    systematic width bias of whatever the fit's residual variance missed by, so
    `sigma_next` is the true per-step sd rather than approximately it.

    Skew and kurtosis survive both operations untouched, which is the point.
    """
    z = std_resid[np.isfinite(std_resid)]
    if z.size < MIN_GARCH_OBS:
        raise GarchFitError(
            f"only {z.size} finite standardized residuals; need {MIN_GARCH_OBS} "
            f"for a bootstrap pool"
        )

    centered = z - z.mean()
    sd = float(np.std(centered))
    if sd <= 0.0:
        raise GarchFitError("standardized residuals have zero spread")
    return centered / sd


def abs_autocorr(x: np.ndarray, lag: int = 1) -> float:
    """Lag-`lag` autocorrelation of `|x|` — the standard volatility-clustering
    statistic. Returns 0.0 when it cannot be computed rather than NaN, so a
    diagnostic never propagates into a snapshot as a null."""
    a = np.abs(np.asarray(x, dtype="float64"))
    a = a[np.isfinite(a)]
    if a.size <= lag + 2:
        return 0.0
    lhs, rhs = a[lag:], a[:-lag]
    sl, sr = float(np.std(lhs)), float(np.std(rhs))
    if sl <= 0.0 or sr <= 0.0:
        return 0.0
    return float(np.mean((lhs - lhs.mean()) * (rhs - rhs.mean())) / (sl * sr))


def _skew(x: np.ndarray) -> float:
    sd = float(np.std(x))
    return 0.0 if sd <= 0.0 else float(np.mean(((x - x.mean()) / sd) ** 3))


def _excess_kurtosis(x: np.ndarray) -> float:
    sd = float(np.std(x))
    return 0.0 if sd <= 0.0 else float(np.mean(((x - x.mean()) / sd) ** 4) - 3.0)
