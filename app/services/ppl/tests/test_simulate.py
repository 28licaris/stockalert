"""Path sampler — determinism, shape, and that sigma_path means what it says."""
from __future__ import annotations

import numpy as np
import pytest

from app.services.ppl.simulate import derive_seed, simulate_paths, thin_paths


def _flat_sigma(h: int, s: float = 0.01) -> np.ndarray:
    return np.full(h, s)


def test_shape_and_anchor_column():
    paths = simulate_paths(100.0, 0.0, _flat_sigma(20), nu=6, n_paths=50, seed=1)
    assert paths.shape == (50, 21)
    assert np.all(paths[:, 0] == 100.0), "every path must start at spot"


def test_same_seed_is_byte_identical():
    a = simulate_paths(100.0, 0.0001, _flat_sigma(30), nu=5, n_paths=200, seed=7)
    b = simulate_paths(100.0, 0.0001, _flat_sigma(30), nu=5, n_paths=200, seed=7)
    np.testing.assert_array_equal(a, b)


def test_different_seeds_differ():
    a = simulate_paths(100.0, 0.0, _flat_sigma(30), nu=5, n_paths=200, seed=7)
    b = simulate_paths(100.0, 0.0, _flat_sigma(30), nu=5, n_paths=200, seed=8)
    assert not np.array_equal(a, b)


def test_terminal_dispersion_matches_sigma_times_sqrt_horizon():
    """The sampler's headline promise: sigma_path is the true per-step sd, so
    a flat sigma over H steps gives sigma*sqrt(H) of terminal log dispersion."""
    h, sigma = 40, 0.01
    paths = simulate_paths(100.0, 0.0, _flat_sigma(h, sigma), nu=25, n_paths=60_000, seed=11)
    realized = float(np.std(np.log(paths[:, -1] / 100.0)))
    assert realized == pytest.approx(sigma * np.sqrt(h), rel=0.05)


def test_drift_moves_the_median_by_mu_times_horizon():
    h, mu = 25, 0.001
    paths = simulate_paths(100.0, mu, _flat_sigma(h), nu=25, n_paths=60_000, seed=12)
    median_log = float(np.median(np.log(paths[:, -1] / 100.0)))
    assert median_log == pytest.approx(mu * h, abs=0.002)


def test_time_varying_sigma_is_respected_per_step():
    """A step with 5x the sigma must show ~5x the dispersion in that step."""
    sigma = np.array([0.002, 0.01, 0.002])
    paths = simulate_paths(100.0, 0.0, sigma, nu=25, n_paths=60_000, seed=13)
    steps = np.diff(np.log(paths), axis=1)
    sds = steps.std(axis=0)
    assert sds[1] / sds[0] == pytest.approx(5.0, rel=0.1)
    assert sds[2] / sds[0] == pytest.approx(1.0, rel=0.1)


def test_fat_tails_produce_wider_extremes_than_gaussian_at_equal_variance():
    h = 20
    fat = simulate_paths(100.0, 0.0, _flat_sigma(h), nu=3.5, n_paths=40_000, seed=14)
    thin = simulate_paths(100.0, 0.0, _flat_sigma(h), nu=30, n_paths=40_000, seed=14)

    fat_tail = np.quantile(fat[:, -1], 0.001)
    thin_tail = np.quantile(thin[:, -1], 0.001)
    assert fat_tail < thin_tail, "a lower nu must reach further into the left tail"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"spot": 0.0},
        {"spot": -5.0},
        {"nu": 2.0},          # variance undefined
        {"n_paths": 0},
    ],
)
def test_rejects_invalid_arguments(kwargs):
    base = dict(spot=100.0, mu=0.0, nu=6.0, n_paths=10, seed=1)
    base.update(kwargs)
    with pytest.raises(ValueError):
        simulate_paths(
            base["spot"], base["mu"], _flat_sigma(5),
            nu=base["nu"], n_paths=base["n_paths"], seed=base["seed"],
        )


@pytest.mark.parametrize("sigma", [np.array([]), np.array([0.01, -0.01]), np.array([np.nan])])
def test_rejects_bad_sigma_paths(sigma):
    with pytest.raises(ValueError):
        simulate_paths(100.0, 0.0, sigma, nu=6, n_paths=10, seed=1)


# ── seeds and thinning ────────────────────────────────────────────────


def test_derive_seed_is_stable_and_distinguishing():
    a = derive_seed("SMH", "30m", "2026-08-06T14:00:00+00:00", "ppl-0.1.0")
    b = derive_seed("SMH", "30m", "2026-08-06T14:00:00+00:00", "ppl-0.1.0")
    c = derive_seed("SMH", "30m", "2026-08-06T14:30:00+00:00", "ppl-0.1.0")
    assert a == b, "same inputs must give the same seed across calls"
    assert a != c, "a different anchor bar must give a different fan"
    assert 0 <= a < 2**63


def test_thinning_returns_at_most_n_render_and_keeps_full_paths():
    paths = simulate_paths(100.0, 0.0, _flat_sigma(10), nu=6, n_paths=1_000, seed=2)
    thinned = thin_paths(paths, 100)
    assert thinned.shape[0] <= 100
    assert thinned.shape[1] == paths.shape[1]
    assert np.all(thinned[:, 0] == 100.0)


def test_thinning_is_a_noop_when_fewer_paths_than_requested():
    paths = simulate_paths(100.0, 0.0, _flat_sigma(10), nu=6, n_paths=50, seed=3)
    np.testing.assert_array_equal(thin_paths(paths, 300), paths)


def test_thinned_sample_is_representative():
    """Thinning must not bias the distribution it is drawn from."""
    paths = simulate_paths(100.0, 0.0, _flat_sigma(30), nu=8, n_paths=20_000, seed=4)
    full_sd = float(np.std(paths[:, -1]))
    thin_sd = float(np.std(thin_paths(paths, 400)[:, -1]))
    assert thin_sd == pytest.approx(full_sd, rel=0.15)
