"""PplService — bars in, `PplSnapshot` out.

The orchestrator. Everything it calls is pure; this is the only file in the
module that does IO, and the only one that knows about `bars_gateway` or
`settings`.

Pipeline:

    bars → conditioning states → drift fit + time-of-day factors
         → deseasonalize → GJR-GARCH fit → FHS path simulation
         → levels → snapshot

The one alignment that matters is `state[i]` conditioning `log_return[i→i+1]`.
It is done once, here, in `_fit`, precisely because getting it wrong anywhere
else would be an undetectable look-ahead bug.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from app.services.ppl import (
    conditioning,
    estimate,
    garch,
    levels as levels_mod,
    simulate,
)
from app.services.ppl.schemas import (
    MODEL_VERSION,
    SUPPORTED_INTERVALS,
    PplParams,
    PplResult,
    PplSnapshot,
    PplState,
    PplVolModel,
)

logger = logging.getLogger(__name__)

_ET = ZoneInfo("America/New_York")

# Seconds per supported interval. Deliberately a local map rather than a reach
# into sim/intervals: PPL supports a subset (spec D1) and should fail loudly on
# anything else rather than inherit intervals it has never been calibrated for.
_INTERVAL_SECONDS: dict[str, int] = {
    "30m": 30 * 60,
    "1h": 60 * 60,
    "4h": 4 * 60 * 60,
    "1d": 24 * 60 * 60,
}

# Bars needed before a fit is worth trusting. Two constraints, and the second
# now binds: the per-state drift estimate wants a few thousand bars across 32
# states, and GJR-GARCH needs `garch.MIN_GARCH_OBS` returns before its four
# parameters are anything but noise. Sized above the GARCH floor so a marginal
# symbol fails the clear `insufficient_data` check rather than the fit.
_MIN_FIT_BARS = max(400, garch.MIN_GARCH_OBS + 100)

# Bars per TRADING day. The equities tier resamples from `ohlcv_1m`, which
# carries the full ET 04:00-20:00 day (16h) — NOT the 6.5h regular session.
# Sizing off 6.5h under-counts every interval and, at 4h, floors to 1 bar/day,
# which turns a bounded window into a full scan of a 131M-row table.
_BARS_PER_TRADING_DAY: dict[str, int] = {"30m": 32, "1h": 16, "4h": 4, "1d": 1}

# ~1.5 calendar days per trading day (weekends + holidays), plus a floor.
_CALENDAR_DAYS_PER_TRADING_DAY = 1.5

# Hard ceiling on the read window. Beyond this the fit gains almost nothing and
# the scan cost is real; CH holds ~8 years of intraday depth anyway.
_MAX_LOOKBACK_DAYS = 3000

BarsFn = Callable[..., list]


class PplService:
    """Builds posterior-predictive snapshots.

    `bars_fn` is injected so the whole pipeline is testable without ClickHouse
    or S3 — tests pass a synthetic bar generator and assert on the snapshot.
    """

    def __init__(self, *, bars_fn: BarsFn) -> None:
        self._bars_fn = bars_fn

    @classmethod
    def from_settings(cls) -> "PplService":
        from app.services.readers.bars_gateway import get_chart_bars

        return cls(bars_fn=get_chart_bars)

    # ── public ────────────────────────────────────────────────────────

    def build(self, symbol: str, params: Optional[PplParams] = None) -> PplResult:
        params = params or PplParams()
        symbol = symbol.upper().strip()

        if params.interval not in SUPPORTED_INTERVALS:
            return PplResult(
                status="error",
                error=(
                    f"interval {params.interval!r} is not supported by PPL. "
                    f"Supported: {', '.join(SUPPORTED_INTERVALS)}."
                ),
                metadata={"symbol": symbol},
            )

        return self.build_from_frame(symbol, self._load_bars(symbol, params), params)

    def build_from_frame(
        self,
        symbol: str,
        frame: Optional[pd.DataFrame],
        params: Optional[PplParams] = None,
    ) -> PplResult:
        """Same pipeline, caller-supplied bars.

        The PIT harness calls this with a truncated prefix so calibration
        exercises the **production path exactly** — a harness that reimplements
        the model validates the reimplementation, not the product.
        """
        params = params or PplParams()
        symbol = symbol.upper().strip()

        if frame is None or frame.empty:
            return PplResult(
                status="no_data",
                error=f"no {params.interval} bars available for {symbol}",
                metadata={"symbol": symbol, "interval": params.interval},
            )

        # Truncate HERE, not in the loader, so every caller fits the same window.
        # The PIT harness hands in an expanding prefix — at 1h that reaches
        # ~35,000 bars against production's 4,000 — and a calibration measured on
        # a window the product never uses is not a calibration of the product.
        # It also matters to the model and not just to cost: a GARCH fit over a
        # 20-year window spanning several volatility regimes is pushed toward
        # spurious IGARCH (Lamoureux & Lastrapes 1990), which is exactly the
        # non-stationary failure this showed up as.
        if len(frame) > params.fit_lookback_bars:
            frame = frame.iloc[-params.fit_lookback_bars :]

        if len(frame) < _MIN_FIT_BARS:
            return PplResult(
                status="insufficient_data",
                error=(
                    f"{symbol} has {len(frame)} {params.interval} bars; "
                    f"PPL needs at least {_MIN_FIT_BARS} to fit"
                ),
                metadata={
                    "symbol": symbol,
                    "interval": params.interval,
                    "available_bars": len(frame),
                    "required_bars": _MIN_FIT_BARS,
                },
            )

        try:
            snapshot = self._build_snapshot(symbol, frame, params)
        except garch.GarchFitError as exc:
            # The volatility model is the product. If it will not fit, there is
            # no honest fan to draw — say so rather than quietly serving a
            # weaker model under the calibrated one's name.
            logger.warning("ppl: %s %s GARCH fit failed — %s", symbol, params.interval, exc)
            return PplResult(
                status="error",
                error=f"volatility model could not be fit for {symbol}: {exc}",
                metadata={
                    "symbol": symbol,
                    "interval": params.interval,
                    "vol_model": params.vol_model,
                    "available_bars": len(frame),
                },
            )

        logger.info(
            "ppl: %s %s built — spot=%.4f state=%s n_fit=%d sigma_h=%.4f%s",
            symbol, params.interval, snapshot.spot, snapshot.state.label,
            snapshot.n_fit_bars, snapshot.sigma_horizon,
            " STALE" if snapshot.stale else "",
        )
        return PplResult(status="ok", snapshot=snapshot, metadata={"symbol": symbol})

    # ── internals ─────────────────────────────────────────────────────

    def _load_bars(self, symbol: str, params: PplParams) -> Optional[pd.DataFrame]:
        """Fetch bars and return an OHLC frame indexed by UTC timestamp."""
        per_day = _BARS_PER_TRADING_DAY[params.interval]
        lookback_days = min(
            _MAX_LOOKBACK_DAYS,
            int(params.fit_lookback_bars / per_day * _CALENDAR_DAYS_PER_TRADING_DAY) + 10,
        )
        # Pass an explicit limit: the reader's auto-limit scales off lookback and
        # would ask for far more rows than the fit window needs. The query keeps
        # the NEWEST `limit` buckets, which is exactly what an anchor wants.
        bars = self._bars_fn(
            symbol,
            interval=params.interval,
            lookback_days=lookback_days,
            limit=params.fit_lookback_bars + 100,
        )
        if not bars:
            return None

        frame = pd.DataFrame(
            {
                "open": [b.open for b in bars],
                "high": [b.high for b in bars],
                "low": [b.low for b in bars],
                "close": [b.close for b in bars],
            },
            index=pd.DatetimeIndex([_utc(b.timestamp) for b in bars], name="timestamp"),
        ).sort_index()

        if params.rth_only:
            frame = restrict_to_rth(frame, params.interval)

        # No truncation here — `build_from_frame` owns it, so the fetched path
        # and the harness path fit an identical window.
        return frame

    def _build_snapshot(
        self, symbol: str, frame: pd.DataFrame, params: PplParams
    ) -> PplSnapshot:
        close, high, low = frame["close"], frame["high"], frame["low"]

        states = conditioning.compute_state_frame(close, high, low, params.conditioning)
        log_ret = np.log(close / close.shift(1)).to_numpy(dtype="float64")

        slots, bars_per_session = _session_slots(frame.index, params.interval)

        # state[i] conditions the return from i to i+1 — shift the return back
        # so it lines up with the state that preceded it.
        forward = np.append(log_ret[1:], np.nan)
        drift = estimate.fit_drift(
            states["code"].to_numpy(dtype="int64"),
            forward,
            kappa=params.shrink_kappa,
            min_state_obs=params.min_state_obs,
        )

        # Time-of-day factors come first either way: the GARCH fit needs a
        # DEseasonalized series (Andersen & Bollerslev 1997), or the optimizer
        # reads the deterministic intraday U-shape as volatility clustering and
        # inflates beta. At 1d this is a no-op array of [1.0].
        tod = estimate.fit_tod_factors(log_ret, slots, bars_per_session)

        anchor_ts = frame.index[-1].to_pydatetime()
        anchor_slot = int(slots[-1])
        spot = float(close.iloc[-1])
        state_code = int(states["code"].iloc[-1])
        state_mu, state_obs, pooled = drift.mu(state_code)
        # Separate the timing claim from the baseline. `state` is the timing
        # claim this platform's evidence rejects; `unconditional` is the asset's
        # own average drift, which PIT showed we cannot set to zero without
        # biasing the fan (spec D9/§6.5).
        mu = {
            "zero": 0.0,
            "unconditional": drift.unconditional,
            "state": state_mu,
        }[params.drift_mode]

        seed = params.seed if params.seed is not None else simulate.derive_seed(
            symbol, params.interval, anchor_ts.isoformat(), MODEL_VERSION
        )
        paths, vol_meta, nu = self._simulate(
            spot, mu, log_ret, tod, slots, bars_per_session, anchor_slot, params, seed
        )

        terminal = paths[:, -1]
        sigma_h = levels_mod.horizon_sigma(terminal, spot)
        min_move = levels_mod.min_move_from_sigma(sigma_h, params.min_move_sigma_mult)

        if params.method == "density":
            extracted = levels_mod.density_levels(terminal, spot, min_move)
        else:
            extracted = levels_mod.quantile_levels(
                terminal, spot, params.quantiles, min_move
            )

        render = simulate.thin_paths(paths, params.n_render_paths)
        stale, stale_age, stale_checked = _staleness(
            anchor_ts, _INTERVAL_SECONDS[params.interval], params.max_stale_bars
        )

        return PplSnapshot(
            symbol=symbol,
            interval=params.interval,
            as_of=anchor_ts,
            spot=spot,
            horizon_bars=params.horizon_bars,
            horizon_end=_horizon_end(
                anchor_ts, anchor_slot, bars_per_session, params.horizon_bars,
                _INTERVAL_SECONDS[params.interval],
            ),
            levels=extracted,
            paths=np.round(render, 4).tolist(),
            terminal_quantiles={
                f"{q:.2f}": float(np.quantile(terminal, q))
                for q in (0.05, 0.15, 0.25, 0.35, 0.5, 0.65, 0.75, 0.85, 0.95)
            },
            terminal_sample=(
                terminal.tolist() if params.include_terminal_sample else None
            ),
            sigma_horizon=sigma_h,
            min_move=min_move,
            vol=vol_meta,
            drift_mode=params.drift_mode,
            drift_per_bar=float(mu),
            state_drift_per_bar=float(state_mu),
            unconditional_drift_per_bar=float(drift.unconditional),
            nu=float(nu),
            bars_per_session=bars_per_session,
            state=PplState(
                code=state_code,
                label=(
                    conditioning.state_label(state_code) if state_code >= 0 else "undefined"
                ),
                psar_side="above" if states["psar"].iloc[-1] > spot else "below",
                psar_value=float(states["psar"].iloc[-1]),
                tsi_value=float(states["tsi"].iloc[-1]),
                tsi_signal_value=float(states["tsi_signal"].iloc[-1]),
                macd_hist=float(states["macd_hist"].iloc[-1]),
                observations=state_obs,
                pooled=pooled,
            ),
            n_fit_bars=len(frame),
            stale=stale,
            stale_age_bars=stale_age,
            staleness_checked=stale_checked,
            seed=seed,
            params=params,
        )

    def _simulate(
        self,
        spot: float,
        mu: float,
        log_ret: np.ndarray,
        tod: np.ndarray,
        slots: np.ndarray,
        bars_per_session: int,
        anchor_slot: int,
        params: PplParams,
        seed: int,
    ) -> tuple[np.ndarray, PplVolModel, float]:
        """Fit the volatility model and draw the paths. Returns
        `(paths, vol_metadata, nu)`.

        The two branches are a real fork in the model, not a config nicety —
        `gjr_fhs` is the product and `ewma_t` is the null it is measured
        against. Keeping both runnable is what makes the comparison in
        `docs/ppl_mathematics.md` a measurement rather than an assertion.
        """
        if params.vol_model == "gjr_fhs":
            return self._simulate_gjr_fhs(
                spot, mu, log_ret, tod, slots, bars_per_session,
                anchor_slot, params, seed,
            )
        return self._simulate_ewma_t(
            spot, mu, log_ret, tod, slots, bars_per_session,
            anchor_slot, params, seed,
        )

    def _simulate_gjr_fhs(
        self, spot, mu, log_ret, tod, slots, bars_per_session,
        anchor_slot, params, seed,
    ) -> tuple[np.ndarray, PplVolModel, float]:
        # Deseasonalize BEFORE fitting: divide out the per-slot volatility shape
        # so what remains is the stochastic part GARCH is meant to model.
        # Simulation re-applies `tod` per step, so nothing is lost.
        deseasonalized = log_ret / tod[slots] if bars_per_session > 1 else log_ret
        fit = garch.fit_gjr_garch(deseasonalized)

        paths = simulate.simulate_fhs_paths(
            spot, mu, fit,
            tod_factors=tod,
            start_slot=anchor_slot,
            bars_per_session=bars_per_session,
            horizon=params.horizon_bars,
            n_paths=params.n_paths,
            seed=seed,
        )

        # Descriptive only — the sampler draws from the pool, never from a
        # Student-t. Reported so the tail stays comparable across models.
        nu = (
            float(params.nu) if params.nu is not None
            else estimate.fit_nu(fit.residual_pool)
        )

        meta = PplVolModel(
            kind="gjr_fhs",
            sigma_next=fit.sigma_next,
            omega=fit.omega,
            alpha=fit.alpha,
            gamma=fit.gamma,
            beta=fit.beta,
            persistence=fit.persistence,
            long_run_sigma=fit.long_run_sigma,
            half_life_bars=fit.half_life_bars,
            leverage_ratio=fit.leverage_ratio,
            vol_response_asymmetry=fit.vol_response_asymmetry,
            variance_ratio=fit.variance_ratio(params.horizon_bars),
            pool_size=int(fit.residual_pool.size),
            pool_skew=fit.residual_skew,
            pool_excess_kurtosis=fit.residual_kurtosis,
            raw_abs_autocorr=fit.raw_abs_autocorr,
            residual_abs_autocorr=fit.residual_abs_autocorr,
        )
        return paths, meta, nu

    def _simulate_ewma_t(
        self, spot, mu, log_ret, tod, slots, bars_per_session,
        anchor_slot, params, seed,
    ) -> tuple[np.ndarray, PplVolModel, float]:
        sigma = estimate.fit_ewma_sigma(log_ret, params.ewma_lambda)
        vol = estimate.VolModel(
            sigma=sigma, tod_factors=tod, bars_per_session=bars_per_session
        )

        if params.nu is not None:
            nu = float(params.nu)
        else:
            sigma_by_bar = (
                sigma * tod[slots] if bars_per_session > 1
                else np.full_like(log_ret, sigma)
            )
            nu = estimate.fit_nu(estimate.standardize(log_ret, sigma_by_bar))

        paths = simulate.simulate_paths(
            spot, mu, vol.path(anchor_slot, params.horizon_bars),
            nu=nu, n_paths=params.n_paths, seed=seed,
        )
        # EWMA is IGARCH: persistence is structurally 1 and variance never
        # mean-reverts, so variance_ratio is 1.0 by construction rather than
        # fitted. Stated rather than left null — it is the model's defining flaw.
        return paths, PplVolModel(
            kind="ewma_t", sigma_next=float(sigma),
            persistence=1.0, variance_ratio=1.0,
        ), nu


# ─────────────────────────────────────────────────────────────────────
# Session geometry
# ─────────────────────────────────────────────────────────────────────


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


# ET regular session, in minutes from midnight: 09:30 (570) → 15:59 (959).
_RTH_OPEN_MIN = 570
_RTH_LAST_MIN = 959


def restrict_to_rth(frame: pd.DataFrame, interval: str) -> pd.DataFrame:
    """Drop bars whose START falls outside the ET regular session.

    **Why this exists.** The live tier resamples CH `ohlcv_1m`, which carries
    the full ET 04:00-20:00 equities day — 32 bars per session at 30m. The
    research tables the PIT harness calibrates against (`ohlcv_30m`,
    `ohlcv_hourly`, `ohlcv_daily`) are RTH-only, 13 bars per session at 30m.
    Two different session geometries mean two different time-of-day profiles,
    two different overnight-gap treatments, and two different volatilities —
    so coverage measured on one would say nothing about the other.

    Restricting the live path to RTH is what makes the calibration transfer.
    Pre/post-market 30m bars are also thin and erratic, so this is the better
    product either way.

    Daily bars already span the session; nothing to restrict.
    """
    if interval == "1d" or frame.empty:
        return frame
    et = frame.index.tz_convert(_ET)
    minutes = et.hour * 60 + et.minute
    return frame[(minutes >= _RTH_OPEN_MIN) & (minutes <= _RTH_LAST_MIN)]


def _session_slots(index: pd.DatetimeIndex, interval: str) -> tuple[np.ndarray, int]:
    """Per-bar position within its ET session, plus the modal session length.

    Grouping by **ET** date rather than UTC date is what keeps after-hours bars
    with the session they belong to — a UTC date rolls over mid-session
    (`docs/standards/data/timezone_et_vs_utc.md`).

    Derived from the data rather than assumed, so early closes and half days
    shorten a session naturally instead of shifting every later slot.
    """
    if interval == "1d":
        return np.zeros(len(index), dtype="int64"), 1

    et_dates = index.tz_convert(_ET).date
    slots = np.empty(len(index), dtype="int64")
    counts: dict[object, int] = {}
    for i, d in enumerate(et_dates):
        slots[i] = counts.get(d, 0)
        counts[d] = slots[i] + 1

    if not counts:
        return slots, 1

    # Modal length, not max: a truncated first or last day in the window would
    # otherwise never define the geometry, but a half day shouldn't either.
    lengths = pd.Series(list(counts.values()))
    bars_per_session = max(1, int(lengths.mode().iloc[0]) if not lengths.empty else 1)

    # A session can still run LONGER than modal — the equities day is ET
    # 04:00-20:00 and pre/post-market coverage is ragged, so the occasional day
    # carries an extra bar. Those overflow bars fold into the final slot rather
    # than indexing past the time-of-day factor array. Folding biases the last
    # slot slightly; the alternative (sizing factors to the max) would leave the
    # forward cycle inserting phantom bars into every normal session, which is
    # worse.
    return np.minimum(slots, bars_per_session - 1), bars_per_session


def _horizon_end(
    anchor_ts: datetime,
    anchor_slot: int,
    bars_per_session: int,
    horizon: int,
    interval_seconds: int,
) -> datetime:
    """Approximate wall-clock end of the simulated horizon.

    A display field: it walks forward over trading sessions so the horizon
    doesn't land on a Saturday, but it does not model early closes. Anything
    that needs exactness should count bars, not clock time.
    """
    if bars_per_session <= 1:
        sessions_ahead, final_slot = horizon, 0
    else:
        total = anchor_slot + horizon
        sessions_ahead, final_slot = divmod(total, bars_per_session)

    naive_end = anchor_ts + timedelta(seconds=interval_seconds * horizon)
    if sessions_ahead == 0:
        return naive_end

    start = anchor_ts.astimezone(_ET).date()
    # Over-fetch: weekends and holidays mean calendar days exceed sessions.
    horizon_days = int(sessions_ahead * 1.7) + 10
    try:
        from app.services.market_calendar import equities_sessions

        # The calendar backend loads lazily inside the call, so guard the call.
        sessions = equities_sessions(start, start + timedelta(days=horizon_days))
    except ImportError:
        logger.warning(
            "ppl: market calendar unavailable — horizon_end falls back to naive "
            "clock arithmetic and may land outside a trading session."
        )
        return naive_end
    except ValueError as exc:
        # The anchor sits outside the calendar's supported range — XNYS starts
        # 2006-08-08, and the PIT harness legitimately anchors before that on
        # long intraday histories. A display field must not take down a fan, and
        # this is a property of the data, not a fault: warn and degrade.
        logger.warning(
            "ppl: market calendar does not cover %s (%s) — horizon_end falls "
            "back to naive clock arithmetic.", start, exc,
        )
        return naive_end

    upcoming = [d for d in sessions if d > start]
    if len(upcoming) < sessions_ahead:
        return naive_end

    target = upcoming[sessions_ahead - 1]
    open_et = datetime.combine(target, datetime.min.time(), tzinfo=_ET).replace(hour=9, minute=30)
    return (open_et + timedelta(seconds=interval_seconds * final_slot)).astimezone(timezone.utc)


def _staleness(
    anchor_ts: datetime, interval_seconds: int, max_stale_bars: int
) -> tuple[bool, Optional[int], bool]:
    """Flag a fan built on an old bar — but only while the market is open.

    Returns `(stale, age_bars, checked)`. Outside regular hours the last
    completed bar is *supposed* to be old, so a wall-clock age check would flag
    every fan built on a weekend; in that case `checked` is False and the other
    two fields carry no information. A frozen feed *during* the session is the
    failure this catches (plan §14.8 — a stale feed produces confident nonsense).
    """
    now = datetime.now(timezone.utc)
    now_et = now.astimezone(_ET)

    try:
        from app.services.market_calendar import is_equities_session

        # The calendar backend loads lazily *inside* this call, so the guard has
        # to wrap the call and not just the import.
        is_trading_day = is_equities_session(now_et.date())
    except ImportError:
        # The calendar backend is a real dependency; losing it is a deployment
        # fault, not a condition to absorb quietly. Say so, and report the check
        # as NOT RUN rather than returning a fresh-looking False.
        logger.warning(
            "ppl: market calendar unavailable — staleness not evaluated for a "
            "bar anchored %s. Snapshot is still valid; the stale flag is not.",
            anchor_ts.isoformat(),
        )
        return False, None, False

    in_session = (
        is_trading_day
        and (now_et.hour, now_et.minute) >= (9, 30)
        and (now_et.hour, now_et.minute) <= (16, 0)
    )
    if not in_session:
        return False, None, False

    age_bars = int(math.floor((now - anchor_ts).total_seconds() / interval_seconds))
    return age_bars > max_stale_bars, age_bars, True
