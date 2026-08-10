"""The forecast half: horizon conversion, annualization, and the ratio."""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np
import pytest

from app.services.ppl.garch import fit_gjr_garch
from app.services.vol_premium.forecast import (
    annualized_forecast_vol,
    calendar_to_trading_days,
    fit_daily_vol,
    forecast_vol_for_expiry,
    vol_premium,
)
from app.services.vol_premium.schemas import TRADING_DAYS_PER_YEAR


def _series(n=3000, sigma=0.011, seed=3):
    """Daily closes from a GJR-GARCH process, in price space."""
    rng = np.random.default_rng(seed)
    w, a, g, b = 2.2e-6, 0.02, 0.13, 0.86
    s2, x = sigma**2, 0.0
    out = [100.0]
    for _ in range(n):
        e = math.sqrt(s2) * rng.standard_normal()
        x += e
        out.append(100.0 * math.exp(x))
        s2 = w + (a + (g if e < 0 else 0.0)) * e * e + b * s2
    return np.array(out)


# ── calendar vs trading days ──────────────────────────────────────────


def test_calendar_days_convert_to_trading_days():
    """30 calendar days is ~21 trading days. Treating it as 30 would stretch
    the horizon by 365/252 and inflate the forecast ~17%."""
    assert calendar_to_trading_days(30) == 21
    assert calendar_to_trading_days(365) == TRADING_DAYS_PER_YEAR
    assert calendar_to_trading_days(7) == 5


def test_short_horizons_never_floor_to_zero():
    assert calendar_to_trading_days(0) == 1
    assert calendar_to_trading_days(1) == 1
    with pytest.raises(ValueError):
        calendar_to_trading_days(-1)


# ── annualization ─────────────────────────────────────────────────────


def test_annualization_matches_sqrt_time_when_variance_ratio_is_one():
    """The identity the closed form has to reduce to: with no mean reversion,
    annualized vol is just sigma * sqrt(252)."""
    fit = fit_gjr_garch(np.diff(np.log(_series())))
    flat = replace(fit, sigma_next=fit.long_run_sigma)   # sits at the long-run level

    got = annualized_forecast_vol(flat, trading_days=21)
    expected = flat.sigma_next * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0

    assert flat.variance_ratio(21) == pytest.approx(1.0, abs=1e-9)
    assert got == pytest.approx(expected, rel=1e-9)


def test_term_structure_slopes_the_right_way_from_both_sides():
    """The reason the horizon is term-matched at all. When today is loud the
    forecast must DECAY with horizon; when quiet it must RISE."""
    fit = fit_gjr_garch(np.diff(np.log(_series())))
    loud = replace(fit, sigma_next=3.0 * fit.long_run_sigma)
    quiet = replace(fit, sigma_next=0.3 * fit.long_run_sigma)

    v = lambda f, d: annualized_forecast_vol(f, trading_days=d)

    assert v(loud, 5) > v(loud, 21) > v(loud, 250)
    assert v(quiet, 5) < v(quiet, 21) < v(quiet, 250)
    # Both converge toward the same long-run level far out.
    far = TRADING_DAYS_PER_YEAR * 20
    assert v(loud, far) == pytest.approx(v(quiet, far), rel=0.05)


def test_naive_sqrt_time_would_overstate_the_far_end_after_a_shock():
    """Quantifies what the retired EWMA model got wrong, and therefore how much
    premium a naive forecast would fabricate on long-dated rows."""
    fit = fit_gjr_garch(np.diff(np.log(_series())))
    loud = replace(fit, sigma_next=3.0 * fit.long_run_sigma)

    term_matched = annualized_forecast_vol(loud, trading_days=126)
    naive = loud.sigma_next * math.sqrt(TRADING_DAYS_PER_YEAR) * 100.0

    assert naive > term_matched * 1.3, "naive scaling is materially too high"


def test_output_is_percent_and_plausible():
    fit = fit_gjr_garch(np.diff(np.log(_series(sigma=0.011))))
    vol = annualized_forecast_vol(fit, trading_days=21)
    assert 3.0 < vol < 120.0, f"{vol} is not a plausible annualized vol in percent"


def test_rejects_impossible_horizons():
    fit = fit_gjr_garch(np.diff(np.log(_series())))
    with pytest.raises(ValueError):
        annualized_forecast_vol(fit, trading_days=0)


# ── fitting from closes ───────────────────────────────────────────────


def test_fit_daily_vol_ignores_non_positive_and_nan_closes():
    c = _series()
    dirty = c.copy()
    dirty[5] = np.nan
    dirty[9] = 0.0
    assert fit_daily_vol(dirty).n_obs > 0


def test_fit_daily_vol_needs_data():
    with pytest.raises(ValueError, match="at least 2"):
        fit_daily_vol(np.array([100.0]))


def test_forecast_for_expiry_threads_the_conversion():
    vol, fit, td = forecast_vol_for_expiry(_series(), calendar_days=30)
    assert td == 21
    assert vol == pytest.approx(annualized_forecast_vol(fit, trading_days=21))


# ── the metric ────────────────────────────────────────────────────────


def test_ratio_is_scale_free_where_points_are_not():
    """Why the ranking uses the ratio: the same 5-point gap is a large edge on a
    quiet name and noise on a loud one, so points cannot order the universe."""
    quiet_ratio, quiet_pts = vol_premium(20.0, 15.0)
    loud_ratio, loud_pts = vol_premium(65.0, 60.0)

    assert quiet_pts == loud_pts == pytest.approx(5.0)
    assert quiet_ratio > loud_ratio, "the ratio separates what points conflate"
    assert quiet_ratio == pytest.approx(20 / 15)


def test_ratio_below_one_means_options_look_cheap():
    ratio, pts = vol_premium(18.0, 24.0)
    assert ratio < 1.0 and pts < 0


def test_zero_forecast_raises_rather_than_dividing():
    with pytest.raises(ValueError):
        vol_premium(25.0, 0.0)
