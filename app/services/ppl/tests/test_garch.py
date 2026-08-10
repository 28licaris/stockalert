"""GJR-GARCH + FHS: does the model do the three things it was built to do?

The EWMA model this replaced failed PIT because it could not reproduce
volatility clustering's consequences, the leverage effect, or variance
mean-reversion. Asserting the new one *has* a gamma parameter proves nothing;
these tests assert it produces the three behaviours, measured the same way they
were measured in the data.

Ground truth throughout is a series simulated from KNOWN GJR-GARCH parameters,
so recovery is checkable rather than plausible.
"""
from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from app.services.ppl.garch import (
    MIN_GARCH_OBS,
    GarchFitError,
    GjrGarchFit,
    abs_autocorr,
    fit_gjr_garch,
)
from app.services.ppl.simulate import simulate_fhs_paths

# Realistic daily-equity parameters: high persistence, most of the news impact
# in the leverage term. sigma_uncond = sqrt(0.02/(1-0.03-0.06-0.90)) ~ 0.63%/bar.
TRUE = dict(omega=0.02, alpha=0.03, gamma=0.12, beta=0.90)


def _simulate_gjr(n: int, seed: int = 0, *, innov_df: float | None = None,
                  **params) -> np.ndarray:
    """Draw a GJR-GARCH series from known parameters, in log-return units.

    `innov_df` swaps the Gaussian innovations for a standardized Student-t, so
    a test can put a KNOWN excess kurtosis into the generator and check that the
    bootstrap pool carries it back out.
    """
    p = {**TRUE, **params}
    w, a, g, b = p["omega"], p["alpha"], p["gamma"], p["beta"]
    rng = np.random.default_rng(seed)

    if innov_df is None:
        draw = rng.standard_normal
    else:
        scale = np.sqrt(innov_df / (innov_df - 2.0))
        def draw():  # noqa: E306 - local, deliberately terse
            return rng.standard_t(innov_df) / scale

    s2 = w / (1 - a - g / 2 - b)
    out = np.empty(n)
    for t in range(n):
        out[t] = np.sqrt(s2) * draw()
        s2 = w + (a + (g if out[t] < 0 else 0.0)) * out[t] ** 2 + b * s2
    return out / 100.0        # percent -> log-return scale


def _flat_tod() -> np.ndarray:
    return np.ones(1, dtype="float64")


# ── the fit recovers what generated the data ──────────────────────────


def test_recovers_known_parameters():
    """The whole model rests on `arch`'s GJR parameterization matching ours.
    Verify the contract rather than trusting the docstring."""
    fit = fit_gjr_garch(_simulate_gjr(60_000, seed=1))

    assert fit.omega == pytest.approx(TRUE["omega"] / 100**2, rel=0.25)
    assert fit.alpha == pytest.approx(TRUE["alpha"], abs=0.02)
    assert fit.gamma == pytest.approx(TRUE["gamma"], abs=0.03)
    assert fit.beta == pytest.approx(TRUE["beta"], abs=0.02)


def test_scale_invariance_of_the_shape_parameters():
    """alpha/beta/gamma must not depend on the units of the input, or the
    adaptive rescaling inside the fit would be changing the model."""
    r = _simulate_gjr(20_000, seed=2)
    a, b = fit_gjr_garch(r), fit_gjr_garch(r * 37.0)

    assert b.alpha == pytest.approx(a.alpha, rel=1e-3)
    assert b.beta == pytest.approx(a.beta, rel=1e-3)
    assert b.gamma == pytest.approx(a.gamma, rel=1e-3)
    # omega carries the units and must scale as the square.
    assert b.omega == pytest.approx(a.omega * 37.0**2, rel=1e-2)


def test_leverage_term_is_detected_and_signed_correctly():
    """gamma > 0 means down moves raise variance more. A symmetric generator
    must NOT produce one."""
    asymmetric = fit_gjr_garch(_simulate_gjr(40_000, seed=3))
    symmetric = fit_gjr_garch(
        _simulate_gjr(40_000, seed=3, alpha=0.09, gamma=0.0)
    )

    assert asymmetric.gamma > 0.05
    assert asymmetric.leverage_ratio > 2.0
    assert symmetric.gamma < 0.03
    assert symmetric.leverage_ratio < 1.5


def test_persistence_is_stationary_and_half_life_is_finite():
    fit = fit_gjr_garch(_simulate_gjr(20_000, seed=4))

    assert 0.0 < fit.persistence < 1.0
    assert fit.persistence == pytest.approx(0.03 + 0.12 / 2 + 0.90, abs=0.02)
    assert 0.0 < fit.half_life_bars < 1000.0
    assert fit.long_run_sigma > 0.0


def test_the_filter_removes_the_volatility_clustering():
    """The premise of bootstrapping residuals as iid. If GJR-GARCH captured the
    dynamics, |z| autocorrelation collapses toward zero from |r|'s."""
    fit = fit_gjr_garch(_simulate_gjr(40_000, seed=5))

    assert fit.raw_abs_autocorr > 0.15, "generator should show clustering"
    assert abs(fit.residual_abs_autocorr) < 0.03
    assert abs(fit.residual_abs_autocorr) < fit.raw_abs_autocorr / 4


def test_pool_is_mean_zero_unit_variance():
    """Drift is DriftModel's alone. A pool with a non-zero mean would apply the
    risk premium a second time on top of it."""
    fit = fit_gjr_garch(_simulate_gjr(20_000, seed=6))

    assert fit.residual_pool.mean() == pytest.approx(0.0, abs=1e-12)
    assert fit.residual_pool.std() == pytest.approx(1.0, rel=1e-12)
    assert fit.residual_pool.size >= MIN_GARCH_OBS


def test_sigma_next_reacts_to_the_sign_of_the_last_bar():
    """The leverage effect has to reach the FIRST forecast bar — a fan built
    after a large down move must start wider than one built after an up move of
    the same size."""
    base = _simulate_gjr(6_000, seed=7)
    shock = 5.0 * float(np.std(base))

    down = fit_gjr_garch(np.append(base, -shock))
    up = fit_gjr_garch(np.append(base, +shock))

    assert down.sigma_next > up.sigma_next * 1.2


def test_variance_ratio_reverts_toward_the_long_run_level_from_both_sides():
    """Mean-reversion, the thing EWMA structurally cannot do.

    The claim is not "the ratio is below some number" — it is that variance
    pulls back toward `long_run_sigma`, so the H-step variance grows slower than
    `H * var(1)` when today is loud and FASTER when today is quiet. EWMA is
    pinned at exactly 1.0 in both directions.
    """
    fit = fit_gjr_garch(_simulate_gjr(20_000, seed=8))

    loud = replace(fit, sigma_next=3.0 * fit.long_run_sigma)
    quiet = replace(fit, sigma_next=0.3 * fit.long_run_sigma)

    for f in (loud, quiet):
        assert f.variance_ratio(1) == pytest.approx(1.0)

    # Bounds are loose on purpose: how FAR the ratio moves by H=40 depends on
    # the fitted half-life (~75 bars here), which is data. That it moves, in the
    # right direction, and keeps moving, is the structural claim.
    assert loud.variance_ratio(40) < 0.90
    assert loud.variance_ratio(100) < loud.variance_ratio(40), "keeps decaying"

    assert quiet.variance_ratio(40) > 1.5
    assert quiet.variance_ratio(100) > quiet.variance_ratio(40)

    # Both converge on the long-run level as H grows, which is what having one
    # means. EWMA has no such level to converge to.
    #
    # H has to be large: the ratio's limit is approached as O(1/H), because the
    # starting transient is a fixed quantity divided by a growing average. With
    # a ~75-bar half-life, H=5000 is still ~17% out — that is arithmetic, not a
    # model defect.
    far = 200_000
    for f in (loud, quiet):
        implied = f.variance_ratio(far) * f.sigma_next**2
        assert implied == pytest.approx(f.long_run_variance, rel=0.02)


# ── failure is loud ───────────────────────────────────────────────────


def test_too_few_observations_raises():
    with pytest.raises(GarchFitError, match="at least"):
        fit_gjr_garch(_simulate_gjr(MIN_GARCH_OBS - 50, seed=9))


def test_all_zero_returns_raises():
    with pytest.raises(GarchFitError):
        fit_gjr_garch(np.zeros(2_000))


def test_nan_returns_are_dropped_not_propagated():
    r = _simulate_gjr(3_000, seed=10)
    r[::100] = np.nan
    fit = fit_gjr_garch(r)

    assert fit.n_obs == 3_000 - 30
    assert np.all(np.isfinite(fit.residual_pool))


# ── the simulation inherits the dynamics ──────────────────────────────


def _fhs(fit: GjrGarchFit, horizon: int, n_paths: int = 40_000, seed: int = 99,
         mu: float = 0.0) -> np.ndarray:
    return simulate_fhs_paths(
        100.0, mu, fit,
        tod_factors=_flat_tod(), start_slot=0, bars_per_session=1,
        horizon=horizon, n_paths=n_paths, seed=seed,
    )


def test_simulation_is_deterministic_under_a_seed():
    fit = fit_gjr_garch(_simulate_gjr(4_000, seed=11))
    assert np.array_equal(_fhs(fit, 20, 500, seed=5), _fhs(fit, 20, 500, seed=5))
    assert not np.array_equal(_fhs(fit, 20, 500, seed=5), _fhs(fit, 20, 500, seed=6))


def test_simulation_shape_and_anchor():
    fit = fit_gjr_garch(_simulate_gjr(4_000, seed=12))
    paths = _fhs(fit, 25, 300)

    assert paths.shape == (300, 26)
    assert np.all(paths[:, 0] == 100.0)
    assert np.all(paths > 0.0), "prices are exp() of log returns and cannot go <= 0"


def test_horizon_skew_grows_rather_than_decaying():
    """The measurement that killed the skewed-innovations hypothesis, run in
    reverse as an acceptance test.

    Under iid innovations the skew of an H-bar sum decays as `g/sqrt(H)`. Under
    GJR-GARCH the leverage feedback compounds it instead, which is the only
    mechanism that reproduces SPY's observed -0.44 -> -1.68 growth from H=1 to
    H=40. Assert the SIGN of the trend, not a level: the level depends on the
    parameters, the direction is the structural claim.
    """
    fit = fit_gjr_garch(_simulate_gjr(40_000, seed=13))

    def skew_at(h: int) -> float:
        term = np.log(_fhs(fit, h, 60_000, seed=21)[:, -1] / 100.0)
        return float(np.mean(((term - term.mean()) / term.std()) ** 3))

    s1, s40 = skew_at(1), skew_at(40)

    assert s40 < -0.15, f"H=40 skew should be materially negative, got {s40:.3f}"
    assert s40 < s1, (
        f"skew must GROW with horizon under leverage: H=1 {s1:.3f} -> H=40 {s40:.3f}"
    )
    # And it must beat what iid innovations could have produced from the same
    # one-bar skew — the 24x discrepancy that made this rewrite necessary.
    assert s40 < s1 / np.sqrt(40) - 0.1


def test_variance_grows_sublinearly_after_a_shock():
    """`var(H) / (H * var(1)) < 1` in the SIMULATION, not just the closed form.
    EWMA's is pinned at 1.0, and at H=40 that made the fan ~18% too wide."""
    base = _simulate_gjr(6_000, seed=14)
    fit = fit_gjr_garch(np.append(base, -8.0 * float(np.std(base))))

    def var_at(h: int) -> float:
        return float(np.var(np.log(_fhs(fit, h, 60_000, seed=22)[:, -1] / 100.0)))

    ratio = var_at(40) / (40 * var_at(1))
    assert ratio < 0.95
    # The simulated ratio should track the closed form the snapshot reports.
    assert ratio == pytest.approx(fit.variance_ratio(40), rel=0.10)


def test_drift_enters_the_mean_and_not_the_variance_recursion():
    """mu must shift the distribution without changing its width — feeding the
    realized return into the GARCH recursion instead of the innovation would
    make persistence depend on the drift setting."""
    fit = fit_gjr_garch(_simulate_gjr(6_000, seed=15))
    h = 30

    flat = np.log(_fhs(fit, h, 60_000, seed=23, mu=0.0)[:, -1] / 100.0)
    tilted = np.log(_fhs(fit, h, 60_000, seed=23, mu=0.001)[:, -1] / 100.0)

    assert (tilted - flat) == pytest.approx(np.full(flat.size, 0.001 * h), abs=1e-12)
    assert tilted.std() == pytest.approx(flat.std(), rel=1e-12)


def test_time_of_day_factors_reshape_the_per_bar_width():
    """The seasonal factor removed before fitting has to come back in
    simulation, on the right bar."""
    fit = fit_gjr_garch(_simulate_gjr(6_000, seed=16))
    tod = np.array([3.0, 0.25, 0.25, 0.5])

    paths = simulate_fhs_paths(
        100.0, 0.0, fit, tod_factors=tod, start_slot=3, bars_per_session=4,
        horizon=4, n_paths=60_000, seed=24,
    )
    step = np.diff(np.log(paths), axis=1)
    # start_slot=3 -> the first simulated bar lands on slot 0, the loud one.
    sd = step.std(axis=0)
    assert sd[0] > 4 * sd[1]
    assert sd[3] == pytest.approx(2 * sd[2], rel=0.15)


def test_pool_recovers_the_innovation_shape_the_data_was_generated_with():
    """FHS's reason to exist. Generate with Student-t(6) innovations — excess
    kurtosis 6/(6-4) = 3.0 — and the pool must come back carrying it, with no
    parametric family fit anywhere in between.

    The Gaussian-QMLE fit is deliberately misspecified here; that it still
    recovers the shape is the Bollerslev-Wooldridge robustness the module
    docstring claims.
    """
    fit = fit_gjr_garch(_simulate_gjr(60_000, seed=17, innov_df=6.0))

    assert fit.residual_kurtosis == pytest.approx(3.0, rel=0.45)
    assert fit.residual_kurtosis > 1.0, "a Student-t pool is not Gaussian"


def test_pool_shape_survives_into_the_terminal_distribution():
    """Whatever shape the pool has, the one-step distribution must inherit it —
    that is the whole point of bootstrapping rather than sampling a family."""
    fit = fit_gjr_garch(_simulate_gjr(40_000, seed=27, innov_df=5.0))

    one_step = np.log(_fhs(fit, 1, 200_000, seed=25)[:, -1] / 100.0)
    z = (one_step - one_step.mean()) / one_step.std()

    assert float(np.mean(z**4) - 3.0) == pytest.approx(fit.residual_kurtosis, rel=0.30)


def test_rejects_impossible_arguments():
    fit = fit_gjr_garch(_simulate_gjr(2_000, seed=18))
    for kwargs in (
        dict(horizon=0), dict(n_paths=0), dict(bars_per_session=0),
    ):
        with pytest.raises(ValueError):
            simulate_fhs_paths(
                100.0, 0.0, fit, tod_factors=_flat_tod(), start_slot=0,
                **{"bars_per_session": 1, "horizon": 5, "n_paths": 10, "seed": 1, **kwargs},
            )

    with pytest.raises(ValueError, match="spot"):
        _fhs_bad = simulate_fhs_paths(
            0.0, 0.0, fit, tod_factors=_flat_tod(), start_slot=0,
            bars_per_session=1, horizon=5, n_paths=10, seed=1,
        )

    with pytest.raises(ValueError, match="tod_factors"):
        simulate_fhs_paths(
            100.0, 0.0, fit, tod_factors=np.ones(2), start_slot=0,
            bars_per_session=4, horizon=5, n_paths=10, seed=1,
        )


# ── the diagnostic helper ─────────────────────────────────────────────


def test_abs_autocorr_matches_a_hand_computation():
    rng = np.random.default_rng(31)
    x = rng.normal(size=5_000)
    a = np.abs(x)
    expected = float(np.corrcoef(a[1:], a[:-1])[0, 1])

    assert abs_autocorr(x) == pytest.approx(expected, abs=1e-12)


def test_abs_autocorr_degenerate_inputs_return_zero_not_nan():
    assert abs_autocorr(np.array([1.0, 2.0])) == 0.0
    assert abs_autocorr(np.ones(100)) == 0.0


def test_vol_response_asymmetry_is_bounded_where_leverage_ratio_is_not():
    """(alpha+gamma)/alpha blows up as alpha -> 0, which is the NORMAL fit for an
    index ETF (SPY daily: alpha ~ 0.003, gamma ~ 0.26). The bounded form has to
    stay readable there, because it is what the UI shows."""
    fit = fit_gjr_garch(_simulate_gjr(20_000, seed=19))

    near_zero_alpha = replace(fit, alpha=1e-9, gamma=0.25)
    assert near_zero_alpha.leverage_ratio > 1e6, "the textbook ratio does blow up"
    assert near_zero_alpha.vol_response_asymmetry == pytest.approx(1.0, abs=1e-6)

    symmetric = replace(fit, alpha=0.09, gamma=0.0)
    assert symmetric.vol_response_asymmetry == pytest.approx(0.5)
    assert symmetric.leverage_ratio == pytest.approx(1.0)

    # Inverse leverage — up moves drive volatility harder. Real for gold, and
    # the measure has to represent it rather than clamp at symmetry.
    inverted = replace(fit, alpha=0.20, gamma=-0.10)
    assert inverted.vol_response_asymmetry < 0.5

    for f in (near_zero_alpha, symmetric, inverted, fit):
        assert 0.0 <= f.vol_response_asymmetry <= 1.0
