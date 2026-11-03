"""Level extraction — quantiles, density modes, and the min_move filter."""
from __future__ import annotations

import numpy as np
import pytest

from app.services.ppl.levels import (
    density_levels,
    find_peaks_simple,
    gaussian_kde_grid,
    horizon_sigma,
    min_move_from_sigma,
    quantile_levels,
)

QUANTILES = (0.15, 0.35, 0.65, 0.85)


def _terminal(spot: float = 100.0, sigma: float = 0.05, n: int = 50_000, seed: int = 1):
    return spot * np.exp(np.random.default_rng(seed).normal(0, sigma, n))


# ── quantile levels ───────────────────────────────────────────────────


def test_quantile_levels_are_ordered_and_roled():
    terminal = _terminal()
    lv = quantile_levels(terminal, 100.0, QUANTILES, min_move=0.0)

    assert [l.price for l in lv] == sorted(l.price for l in lv)
    assert [(l.side, l.rank) for l in lv] == [
        ("down", "outer"), ("down", "inner"), ("up", "inner"), ("up", "outer")
    ]
    assert [l.quantile for l in lv] == list(QUANTILES)


def test_quantile_levels_report_distance_from_spot():
    terminal = _terminal()
    lv = quantile_levels(terminal, 100.0, QUANTILES, min_move=0.0)
    for level in lv:
        assert level.pct_from_spot == pytest.approx(level.price / 100.0 - 1.0)
    assert lv[0].pct_from_spot < 0 < lv[-1].pct_from_spot


def test_levels_inside_min_move_are_flagged_not_dropped():
    """A filtered level is still a reported outcome — no silent failures."""
    terminal = _terminal(sigma=0.01)
    lv = quantile_levels(terminal, 100.0, QUANTILES, min_move=0.05)

    assert len(lv) == 4, "suppressed levels must still be returned"
    assert all(l.suppressed for l in lv)
    assert all(l.suppressed_reason and "min_move" in l.suppressed_reason for l in lv)


def test_wide_levels_are_not_suppressed():
    lv = quantile_levels(_terminal(sigma=0.20), 100.0, QUANTILES, min_move=0.01)
    assert not any(l.suppressed for l in lv)
    assert all(l.suppressed_reason is None for l in lv)


@pytest.mark.parametrize("bad", [(0.1, 0.2, 0.3), (0.0, 0.3, 0.6, 0.9), (0.1, 0.4, 0.6, 1.0)])
def test_quantile_levels_reject_bad_quantiles(bad):
    with pytest.raises(ValueError):
        quantile_levels(_terminal(), 100.0, bad, min_move=0.0)


def test_a_drifting_distribution_can_put_a_down_level_above_spot():
    """`side` describes position in the forecast, not relative to spot. Under a
    strong drift the 35th percentile can sit above spot — that is information,
    and the sign of pct_from_spot carries it."""
    terminal = _terminal(spot=100.0, sigma=0.02) * 1.10
    lv = quantile_levels(terminal, 100.0, QUANTILES, min_move=0.0)
    down_inner = next(l for l in lv if l.side == "down" and l.rank == "inner")
    assert down_inner.pct_from_spot > 0


# ── min_move ──────────────────────────────────────────────────────────


def test_horizon_sigma_recovers_the_generating_volatility():
    assert horizon_sigma(_terminal(sigma=0.06), 100.0) == pytest.approx(0.06, rel=0.02)


def test_min_move_scales_with_horizon_sigma():
    """This is what reproduces the reference's 1.57% -> 3.71% across
    timeframes without a per-symbol constant."""
    near = min_move_from_sigma(0.06, 0.25)
    far = min_move_from_sigma(0.06 * 2, 0.25)
    assert far == pytest.approx(near * 2)
    assert near == pytest.approx(0.015)


def test_horizon_sigma_rejects_bad_spot():
    with pytest.raises(ValueError):
        horizon_sigma(_terminal(), 0.0)


# ── density levels ────────────────────────────────────────────────────


def test_density_levels_find_both_modes_of_a_bimodal_cloud():
    rng = np.random.default_rng(2)
    terminal = np.concatenate([rng.normal(90, 1.5, 20_000), rng.normal(115, 1.5, 20_000)])
    lv = density_levels(terminal, 100.0, min_move=0.0)

    prices = sorted(l.price for l in lv)
    assert len(prices) >= 2
    assert prices[0] == pytest.approx(90, abs=3)
    assert prices[-1] == pytest.approx(115, abs=3)


def test_density_levels_do_not_invent_modes_in_a_unimodal_cloud():
    lv = density_levels(_terminal(sigma=0.05), 100.0, min_move=0.0)
    assert len(lv) <= 2, f"a unimodal cloud should not yield 4 modes, got {len(lv)}"


def test_density_levels_respect_min_move():
    rng = np.random.default_rng(3)
    terminal = np.concatenate([rng.normal(99.5, 0.3, 10_000), rng.normal(100.5, 0.3, 10_000)])
    lv = density_levels(terminal, 100.0, min_move=0.05)
    assert lv and all(l.suppressed for l in lv)


# ── numeric helpers (in-repo scipy replacements) ──────────────────────


def test_kde_integrates_to_one():
    samples = np.random.default_rng(4).normal(0, 1, 20_000)
    grid = np.linspace(-6, 6, 1_000)
    dens = gaussian_kde_grid(samples, grid)
    assert np.trapezoid(dens, grid) == pytest.approx(1.0, rel=0.02)


def test_kde_peaks_at_the_true_mode():
    samples = np.random.default_rng(5).normal(3.0, 1.0, 20_000)
    grid = np.linspace(-2, 8, 500)
    assert grid[int(np.argmax(gaussian_kde_grid(samples, grid)))] == pytest.approx(3.0, abs=0.3)


def test_kde_needs_samples():
    with pytest.raises(ValueError):
        gaussian_kde_grid(np.array([1.0]), np.linspace(0, 2, 10))


def test_find_peaks_locates_local_maxima():
    y = np.array([0.0, 1.0, 0.0, 2.0, 0.0, 0.5, 0.0])
    assert find_peaks_simple(y).tolist() == [1, 3, 5]


def test_find_peaks_prominence_filters_small_bumps():
    y = np.array([0.0, 1.0, 0.0, 2.0, 0.0, 0.05, 0.0])
    assert find_peaks_simple(y, prominence=0.5).tolist() == [1, 3]


def test_find_peaks_handles_degenerate_input():
    assert find_peaks_simple(np.array([1.0, 2.0])).size == 0
    assert find_peaks_simple(np.zeros(10)).size == 0
