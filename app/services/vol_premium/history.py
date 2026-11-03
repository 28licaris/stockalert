"""Historical volatility forecast — the other half of the percentile. Pure.

`options_atm_iv_daily` gives ten years of what options **cost**. To say whether
today's premium is unusual we need ten years of what the stock **did** —
a forecast for every historical day, computed the way the live screen computes
today's.

## Why parameters are refit monthly, not daily

A fit per day is ~87,000 GJR-GARCH fits at ~0.2s each: about five hours, and
it would be answering a question nobody asked. GARCH parameters are slow-moving
structural estimates; the fast-moving part is the conditional variance, and
that has a closed-form daily update:

    sigma^2_{t+1} = omega + (alpha + gamma*1[eps_t < 0]) * eps_t^2 + beta * sigma^2_t

So: refit the parameters at each month boundary on the trailing window, then
roll the variance forward **daily** off realized returns in between. ~5,080
fits instead of ~87,000, and it is also closer to how the model actually runs
in production, where a fit is reused until the next scheduled one.

## No look-ahead

Each month's parameters are fit on bars strictly **before** that month begins,
and the variance rolled into day *t* uses returns up to *t-1*. A forecast for
day *t* therefore never sees day *t*. This is the property that makes the
resulting percentile honest rather than a backtest of hindsight.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional, Sequence

import numpy as np

from app.services.ppl.garch import (
    GarchFitError,
    GjrGarchFit,
    fit_gjr_garch,
    variance_ratio_from,
)
from app.services.vol_premium.forecast import calendar_to_trading_days
from app.services.vol_premium.schemas import TRADING_DAYS_PER_YEAR

logger = logging.getLogger(__name__)

# Below this a fit is reading noise; the walk reports a gap rather than guessing.
MIN_FIT_BARS = 600
DEFAULT_LOOKBACK = 4000


@dataclass(frozen=True)
class ForecastPoint:
    """One day's annualized forecast, with the provenance to audit it."""

    eod_date: date
    forecast_vol: float          # percent, annualized at this row's horizon
    sigma_next: float            # per-bar conditional sd rolled to this day
    variance_ratio: float
    trading_days: int
    persistence: float
    fit_date: date               # when the parameters behind it were fit


def rolling_forecasts(
    dates: Sequence[date],
    closes: Sequence[float],
    targets: dict[date, int],
    *,
    lookback: int = DEFAULT_LOOKBACK,
    min_fit_bars: int = MIN_FIT_BARS,
) -> tuple[list[ForecastPoint], dict[str, int]]:
    """Walk a daily series, refitting monthly and rolling variance daily.

    `targets` maps a date to that row's **calendar** days-to-expiry, so each
    day is forecast at the horizon its own option actually had.

    Returns `(points, skip_counts)`. Days that could not be forecast are
    counted by reason rather than silently omitted.
    """
    d = np.asarray(dates, dtype=object)
    c = np.asarray(closes, dtype="float64")
    if d.size != c.size:
        raise ValueError(f"dates {d.size} and closes {c.size} must align")
    if d.size < 2:
        return [], {"too_few_bars": int(d.size)}

    # r[i] is the return INTO day i, so it is known at the close of day i.
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.concatenate([[np.nan], np.diff(np.log(c))])

    out: list[ForecastPoint] = []
    skips: dict[str, int] = {}
    fit: Optional[GjrGarchFit] = None
    fit_on: Optional[date] = None
    fit_month: Optional[tuple[int, int]] = None
    sigma2 = np.nan

    for i, day in enumerate(d):
        month = (day.year, day.month)

        if fit_month != month:
            # Refit on bars strictly BEFORE this month — no look-ahead.
            window = r[max(0, i - lookback):i]
            window = window[np.isfinite(window)]
            fit_month = month
            if window.size < min_fit_bars:
                fit = None
                skips["insufficient_history"] = skips.get("insufficient_history", 0) + 1
            else:
                try:
                    fit = fit_gjr_garch(window)
                    fit_on = day
                    # Seed the roll from the fit's own one-step-ahead variance.
                    sigma2 = fit.sigma_next**2
                except GarchFitError:
                    fit = None
                    skips["fit_failed"] = skips.get("fit_failed", 0) + 1

        if fit is None:
            continue

        dte = targets.get(day)
        if dte is None:
            continue

        if np.isfinite(sigma2) and sigma2 > 0:
            td = calendar_to_trading_days(dte)
            vr = variance_ratio_from(sigma2, fit.persistence, fit.long_run_variance, td)
            vol = float(np.sqrt(sigma2) * np.sqrt(TRADING_DAYS_PER_YEAR * vr) * 100.0)
            out.append(ForecastPoint(
                eod_date=day, forecast_vol=vol, sigma_next=float(np.sqrt(sigma2)),
                variance_ratio=float(vr), trading_days=td,
                persistence=fit.persistence, fit_date=fit_on or day,
            ))
        else:
            skips["bad_variance"] = skips.get("bad_variance", 0) + 1

        # Roll to tomorrow using TODAY's realized return — never tomorrow's.
        eps = r[i]
        if np.isfinite(eps) and np.isfinite(sigma2):
            lev = fit.gamma if eps < 0 else 0.0
            sigma2 = fit.omega + (fit.alpha + lev) * eps * eps + fit.beta * sigma2

    logger.debug("history: %d forecasts, skips=%s", len(out), skips)
    return out, skips


def percentile_rank(value: float, history: Iterable[float]) -> Optional[float]:
    """Where `value` sits in `history`, 0-100. None on an empty history.

    None rather than a default, because "no baseline" and "unremarkable" are
    opposite claims and must not share a number.
    """
    h = np.asarray([x for x in history if x is not None and np.isfinite(x)], dtype="float64")
    if h.size == 0:
        return None
    return float((h < value).sum() / h.size * 100.0)
