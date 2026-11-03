"""The rolling historical forecast.

The property that matters most is **no look-ahead**: a forecast for day t must
not use day t's return, or the percentile built on top is a backtest of
hindsight rather than a measurement.
"""
from __future__ import annotations

import math
from datetime import date, timedelta

import numpy as np
import pytest

from app.services.vol_premium.history import (
    MIN_FIT_BARS,
    percentile_rank,
    rolling_forecasts,
)


def series(n=1800, sigma=0.011, seed=5, start=date(2020, 1, 1)):
    """Daily (dates, closes) from a GJR-GARCH process."""
    rng = np.random.default_rng(seed)
    w, a, g, b = 2.2e-6, 0.02, 0.13, 0.86
    s2, x = sigma**2, 0.0
    closes, dates, d = [100.0], [start], start
    for _ in range(n):
        e = math.sqrt(s2) * rng.standard_normal()
        x += e
        d += timedelta(days=1)
        if d.weekday() < 5:
            closes.append(100.0 * math.exp(x))
            dates.append(d)
        s2 = w + (a + (g if e < 0 else 0.0)) * e * e + b * s2
    return dates, closes


def targets_for(dates, dte=30, after=MIN_FIT_BARS + 100):
    return {d: dte for d in dates[after:]}


# ── the core property ─────────────────────────────────────────────────


def test_no_lookahead_future_returns_cannot_change_a_past_forecast():
    """Rewrite history AFTER a given day; that day's forecast must not move.

    This is the test that makes the percentile trustworthy. If it ever fails,
    every number built on this module is contaminated.
    """
    dates, closes = series(n=3000)
    tgt = targets_for(dates)

    base, _ = rolling_forecasts(dates, closes, tgt)
    assert base, "expected forecasts"

    # Cut late enough that a long stretch of forecasts already exists before it.
    cut = int(len(dates) * 0.8)
    tampered = list(closes)
    for i in range(cut, len(tampered)):          # violent future, same past
        tampered[i] *= 1.0 + 0.25 * ((i % 2) - 0.5)

    after, _ = rolling_forecasts(dates, tampered, tgt)

    bmap = {p.eod_date: p.forecast_vol for p in base}
    amap = {p.eod_date: p.forecast_vol for p in after}
    shared_past = [d for d in bmap if d in amap and d < dates[cut]]
    assert len(shared_past) > 50, "need a meaningful past window to compare"
    for d in shared_past:
        assert bmap[d] == pytest.approx(amap[d], rel=1e-12), f"{d} changed"


def test_forecast_for_a_day_excludes_that_days_return():
    """Same prefix, different value for the LAST day — the forecast stamped on
    that day is computed before the day's own move is known, so it is equal."""
    dates, closes = series(n=900)
    tgt = {dates[-1]: 30}

    a, _ = rolling_forecasts(dates, closes, tgt)
    bumped = list(closes)
    bumped[-1] *= 1.15
    b, _ = rolling_forecasts(dates, bumped, tgt)

    assert a and b
    assert a[-1].forecast_vol == pytest.approx(b[-1].forecast_vol, rel=1e-12)


# ── behaviour ─────────────────────────────────────────────────────────


def test_produces_a_forecast_per_target_day():
    dates, closes = series()
    tgt = targets_for(dates)
    pts, skips = rolling_forecasts(dates, closes, tgt)

    assert len(pts) > 0.9 * len(tgt), f"{len(pts)} of {len(tgt)} ({skips})"
    assert all(0 < p.forecast_vol < 300 for p in pts)
    assert all(0 < p.persistence < 1 for p in pts)
    assert all(p.trading_days == 21 for p in pts), "30 calendar days ≈ 21 trading"


def test_parameters_are_refit_monthly_not_daily():
    """The whole reason this module exists — a fit per day would be ~5 hours."""
    dates, closes = series()
    pts, _ = rolling_forecasts(dates, closes, targets_for(dates))

    fits = {p.fit_date for p in pts}
    months = {(p.eod_date.year, p.eod_date.month) for p in pts}
    assert len(fits) == len(months), "expected exactly one refit per month"
    assert len(fits) < len(pts) / 10, "refits must be far rarer than forecasts"


def test_variance_rolls_daily_between_refits():
    """Within a month the parameters are fixed but sigma must still move,
    otherwise the forecast is a monthly step function."""
    dates, closes = series()
    pts, _ = rolling_forecasts(dates, closes, targets_for(dates))

    by_fit: dict = {}
    for p in pts:
        by_fit.setdefault(p.fit_date, []).append(p.sigma_next)
    month = max(by_fit.values(), key=len)
    assert len(set(month)) > 1, "sigma is frozen within the month"


def test_horizon_is_taken_per_row():
    dates, closes = series()
    short = {d: 7 for d in dates[400:]}
    long = {d: 60 for d in dates[400:]}

    s, _ = rolling_forecasts(dates, closes, short)
    l, _ = rolling_forecasts(dates, closes, long)

    assert s[0].trading_days == 5 and l[0].trading_days == 41
    assert s[0].forecast_vol != l[0].forecast_vol


# ── gaps are reported, not hidden ─────────────────────────────────────


def test_insufficient_history_is_counted_not_silently_dropped():
    dates, closes = series(n=200)
    pts, skips = rolling_forecasts(dates, closes, {d: 30 for d in dates})

    assert pts == []
    assert skips.get("insufficient_history", 0) > 0


def test_mismatched_inputs_raise():
    dates, closes = series(n=300)
    with pytest.raises(ValueError, match="align"):
        rolling_forecasts(dates, closes[:-1], {})


def test_days_without_a_target_are_simply_not_forecast():
    dates, closes = series()
    only = dates[800]
    pts, _ = rolling_forecasts(dates, closes, {only: 30})
    assert [p.eod_date for p in pts] == [only]


# ── percentile ────────────────────────────────────────────────────────


def test_percentile_rank_basics():
    h = list(range(100))
    assert percentile_rank(50, h) == pytest.approx(50.0)
    assert percentile_rank(-5, h) == 0.0
    assert percentile_rank(999, h) == 100.0


def test_percentile_rank_ignores_nans_and_empty_is_none():
    assert percentile_rank(1.0, []) is None
    assert percentile_rank(1.0, [float("nan"), None]) is None
    assert percentile_rank(2.0, [1.0, float("nan"), 3.0]) == pytest.approx(50.0)
