"""Drift shrinkage, EWMA volatility, time-of-day factors, tail index."""
from __future__ import annotations

import numpy as np
import pytest

from app.services.ppl.estimate import (
    NU_CEILING,
    NU_FLOOR,
    VolModel,
    fit_drift,
    fit_ewma_sigma,
    fit_nu,
    fit_tod_factors,
    standardize,
)


# ── drift ─────────────────────────────────────────────────────────────


def test_drift_shrinks_toward_zero_by_the_documented_factor():
    """mu = mean * n/(n+kappa) — checked against a hand-computable case."""
    states = np.zeros(100, dtype="int64")
    returns = np.full(100, 0.01)

    model = fit_drift(states, returns, kappa=200.0, min_state_obs=1)
    mu, n, pooled = model.mu(0)

    assert n == 100 and not pooled
    assert mu == pytest.approx(0.01 * 100 / 300)
    assert abs(mu) < 0.01, "shrinkage must pull the estimate toward zero"


def test_more_observations_shrink_less():
    thin = fit_drift(np.zeros(50, "int64"), np.full(50, 0.01), kappa=200.0, min_state_obs=1)
    thick = fit_drift(np.zeros(5000, "int64"), np.full(5000, 0.01), kappa=200.0, min_state_obs=1)
    assert thick.mu(0)[0] > thin.mu(0)[0]
    assert thick.mu(0)[0] == pytest.approx(0.01, rel=0.05)


def test_thin_states_pool_and_say_so():
    """A state below min_state_obs must fall back to the unconditional mean and
    report `pooled` — never silently borrow another state's estimate."""
    states = np.array([0] * 500 + [7] * 3, dtype="int64")
    returns = np.concatenate([np.full(500, 0.001), np.full(3, 0.5)])

    model = fit_drift(states, returns, kappa=200.0, min_state_obs=50)
    mu, n, pooled = model.mu(7)

    assert pooled is True and n == 3
    assert mu == model.unconditional
    assert mu < 0.01, "the 3-observation outlier must not drive the estimate"


def test_unknown_state_pools():
    model = fit_drift(np.zeros(500, "int64"), np.full(500, 0.001), kappa=200.0, min_state_obs=50)
    mu, n, pooled = model.mu(31)
    assert pooled is True and n == 0 and mu == model.unconditional


def test_drift_ignores_undefined_states_and_nan_returns():
    states = np.array([-1, -1, 0, 0, 0], dtype="int64")
    returns = np.array([99.0, 99.0, 0.01, 0.01, np.nan])
    model = fit_drift(states, returns, kappa=0.0, min_state_obs=1)
    assert model.counts == {0: 2}
    assert model.mu(0)[0] == pytest.approx(0.01)


def test_drift_rejects_misaligned_inputs():
    with pytest.raises(ValueError):
        fit_drift(np.zeros(10, "int64"), np.zeros(9), kappa=1.0, min_state_obs=1)


def test_empty_input_yields_a_zero_model():
    model = fit_drift(np.array([], dtype="int64"), np.array([]), kappa=1.0, min_state_obs=1)
    assert model.mu(0) == (0.0, 0, True)


# ── volatility ────────────────────────────────────────────────────────


def test_ewma_sigma_recovers_a_known_volatility():
    rng = np.random.default_rng(3)
    r = rng.normal(0.0, 0.01, 20_000)
    assert fit_ewma_sigma(r, 0.94) == pytest.approx(0.01, rel=0.25)


def test_ewma_sigma_tracks_a_regime_shift():
    """EWMA must weight the recent past — that's the whole point of it."""
    rng = np.random.default_rng(4)
    calm_then_wild = np.concatenate(
        [rng.normal(0, 0.002, 5_000), rng.normal(0, 0.02, 500)]
    )
    assert fit_ewma_sigma(calm_then_wild, 0.94) > 0.01


def test_ewma_sigma_needs_data():
    with pytest.raises(ValueError):
        fit_ewma_sigma(np.array([0.01]), 0.94)


def test_tod_factors_average_to_one_and_track_the_u_shape():
    rng = np.random.default_rng(5)
    bars_per_session, n_sessions = 13, 400
    slots = np.tile(np.arange(bars_per_session), n_sessions)

    # Open and close genuinely more volatile than midday.
    scale = np.ones(bars_per_session) * 0.005
    scale[0] = scale[-1] = 0.015
    r = rng.normal(0, scale[slots])

    factors = fit_tod_factors(r, slots, bars_per_session)

    assert factors.mean() == pytest.approx(1.0)
    assert factors[0] > factors[6], "the open must carry a higher factor than midday"
    assert factors[-1] > factors[6], "so must the close"


def test_tod_factors_are_flat_for_daily_bars():
    factors = fit_tod_factors(np.random.default_rng(6).normal(0, 0.01, 500), np.zeros(500, "int64"), 1)
    assert factors.tolist() == [1.0]


def test_tod_slots_without_enough_data_fall_back_to_one():
    slots = np.concatenate([np.zeros(500, "int64"), np.ones(3, "int64")])
    r = np.concatenate([np.full(500, 0.01), np.full(3, 5.0)])
    factors = fit_tod_factors(r, slots, 2)
    assert np.all(np.isfinite(factors))
    assert factors[1] < factors[0] * 10, "3 observations must not define a slot"


def test_vol_path_applies_the_tod_factor_in_order():
    vol = VolModel(sigma=0.01, tod_factors=np.array([2.0, 0.5, 1.0, 0.5]), bars_per_session=4)
    path = vol.path(start_slot=0, horizon=4)
    # Starting at slot 0, the next four bars are slots 1, 2, 3, 0.
    np.testing.assert_allclose(path, [0.005, 0.01, 0.005, 0.02])


def test_vol_path_is_flat_when_there_is_one_bar_per_session():
    vol = VolModel(sigma=0.02, tod_factors=np.array([1.0]), bars_per_session=1)
    np.testing.assert_allclose(vol.path(0, 5), np.full(5, 0.02))


# ── tail index ────────────────────────────────────────────────────────


def test_nu_hits_the_ceiling_for_gaussian_data():
    r = np.random.default_rng(7).normal(0, 1, 200_000)
    assert fit_nu(r) > 15.0, "Gaussian data has no fat tail to report"


def test_fatter_tails_produce_a_lower_nu():
    rng = np.random.default_rng(8)
    heavy = fit_nu(rng.standard_t(5, 200_000))
    light = fit_nu(rng.standard_t(25, 200_000))
    assert heavy < light, f"t(5) should fit a lower nu than t(25), got {heavy} vs {light}"


def test_nu_is_clamped_to_a_finite_variance_range():
    rng = np.random.default_rng(9)
    for df in (2.5, 3, 5, 10, 50):
        nu = fit_nu(rng.standard_t(df, 50_000))
        assert NU_FLOOR <= nu <= NU_CEILING


def test_nu_defaults_when_there_is_too_little_data():
    assert fit_nu(np.random.default_rng(10).normal(0, 1, 10)) == 4.0


def test_standardize_divides_by_the_per_bar_sigma():
    r = np.array([0.02, -0.01, 0.0])
    sigma = np.array([0.01, 0.01, 0.0])
    out = standardize(r, sigma)
    np.testing.assert_allclose(out[:2], [2.0, -1.0])
    assert np.isnan(out[2]), "a zero sigma must produce NaN, not an infinity"
