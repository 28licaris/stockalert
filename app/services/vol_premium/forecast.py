"""Daily bars → an annualized volatility forecast at an option's horizon. Pure.

This is the denominator of the whole product, and it is the reason the PPL
calibration work had to come first: comparing an option's price against an
*uncalibrated* volatility forecast produces a ratio that looks meaningful and
is not.

## Why the horizon matters

An option expiring in 7 days and one expiring in 60 are not asking the same
question, so they cannot be compared against the same forecast. GJR-GARCH gives
a mean-reverting term structure, which is exactly what is needed:

    var(H) = H * sigma_next^2 * variance_ratio(H)

Annualizing multiplies by `252 / H`, and the `H` cancels:

    sigma_annual = sigma_next * sqrt(252 * variance_ratio(H))

`variance_ratio(H)` is below 1 when today is louder than the symbol's long-run
level and above 1 when quieter (see `app/services/ppl/garch.py`). The naive
alternative — assume variance grows linearly, i.e. `variance_ratio == 1` at
every horizon — is what the retired EWMA model did, and it overstates the far
end of the curve. Used here it would fabricate premium on every long-dated row.

## Calendar days are not trading days

Schwab reports `daysToExpiration` in **calendar** days; the GARCH fit is defined
over **trading** bars. Feeding 30 calendar days in as 30 trading days stretches
the horizon by 365/252 and inflates the forecast by roughly 17% — enough to turn
a genuinely expensive option into a cheap-looking one.
"""
from __future__ import annotations

import math
from typing import Optional

import numpy as np

from app.services.ppl.garch import GjrGarchFit, fit_gjr_garch
from app.services.vol_premium.schemas import (
    CALENDAR_DAYS_PER_YEAR,
    TRADING_DAYS_PER_YEAR,
)


def calendar_to_trading_days(calendar_days: int) -> int:
    """Convert an option's calendar days-to-expiry to trading bars.

    Floored at 1: a zero-day horizon has no variance to forecast, and rounding
    a 1-day option to 0 would divide by zero downstream.
    """
    if calendar_days < 0:
        raise ValueError(f"calendar_days must be non-negative, got {calendar_days}")
    scaled = calendar_days * TRADING_DAYS_PER_YEAR / CALENDAR_DAYS_PER_YEAR
    return max(1, int(round(scaled)))


def annualized_forecast_vol(fit: GjrGarchFit, *, trading_days: int) -> float:
    """Annualized volatility over `trading_days`, in **percent**.

    Percent to match the convention Schwab reports implied volatility in, so
    the two halves of the ratio are directly comparable without a unit
    conversion hiding at the call site.
    """
    if trading_days < 1:
        raise ValueError(f"trading_days must be >= 1, got {trading_days}")

    vr = fit.variance_ratio(trading_days)
    if not math.isfinite(vr) or vr <= 0:
        raise ValueError(f"variance_ratio({trading_days}) is not usable: {vr}")

    return float(fit.sigma_next * math.sqrt(TRADING_DAYS_PER_YEAR * vr) * 100.0)


def fit_daily_vol(closes: np.ndarray) -> GjrGarchFit:
    """Fit the volatility model to a daily close series.

    Daily bars need no deseasonalizing — the intraday time-of-day shape PPL
    divides out at 30m/1h does not exist at this frequency, so the log returns
    go straight into the fit. Same estimator and same window as `/ppl`, so the
    two surfaces can never disagree about the same symbol.
    """
    c = np.asarray(closes, dtype="float64")
    c = c[np.isfinite(c) & (c > 0)]
    if c.size < 2:
        raise ValueError(f"need at least 2 positive closes, got {c.size}")

    return fit_gjr_garch(np.diff(np.log(c)))


def forecast_vol_for_expiry(
    closes: np.ndarray, *, calendar_days: int
) -> tuple[float, GjrGarchFit, int]:
    """Convenience: closes + an option's calendar DTE → `(vol%, fit, trading_days)`."""
    trading_days = calendar_to_trading_days(calendar_days)
    fit = fit_daily_vol(closes)
    return annualized_forecast_vol(fit, trading_days=trading_days), fit, trading_days


def vol_premium(implied_vol: float, forecast_vol: float) -> tuple[float, float]:
    """`(ratio, points)` — the ranking column and the sizing column.

    The ratio leads because it is scale-free: a 5-point gap is a rounding error
    on a 60-vol semiconductor and a large edge on a 15-vol utility, so only the
    ratio orders the whole universe coherently. The points figure is what a
    trader actually sizes against, so both are reported.
    """
    if forecast_vol <= 0:
        raise ValueError(f"forecast_vol must be positive, got {forecast_vol}")
    return implied_vol / forecast_vol, implied_vol - forecast_vol
