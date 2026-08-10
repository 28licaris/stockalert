"""Posterior Predictive Levels — public DTOs.

The only file other services import (plus `contract.py`). See
`docs/posterior_predictive_levels_spec.md`.
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Bumped whenever the model changes in a way that invalidates a cached
# snapshot or a calibration run. Part of the cache key and the seed.
MODEL_VERSION = "ppl-0.2.0"

SUPPORTED_INTERVALS: tuple[str, ...] = ("30m", "1h", "4h", "1d")

LevelSide = Literal["up", "down"]
LevelRank = Literal["inner", "outer"]
LevelMethod = Literal["quantile", "density"]
PplStatus = Literal["ok", "insufficient_data", "no_data", "error"]

# How volatility evolves over the horizon. Not a tuning knob — one of these is
# the model and the other is the null it was measured against.
#
# "gjr_fhs"  — GJR-GARCH(1,1) volatility dynamics with Filtered Historical
#              Simulation innovations. Volatility clusters, mean-reverts, and
#              responds asymmetrically to down moves; innovations are
#              bootstrapped from the fitted residuals with no shape imposed.
# "ewma_t"   — the previous model: constant volatility over the horizon, iid
#              Student-t innovations. Retained so the PIT harness can measure
#              the difference rather than take it on faith. Its persistence is
#              structurally 1 (EWMA is IGARCH), so its fan grows as sqrt(H)
#              without bound and is symmetric by construction.
#
# See `app/services/ppl/garch.py` for the measurements that decided this.
VolModelKind = Literal["gjr_fhs", "ewma_t"]

# Three separable claims, only one of which the evidence rejects:
#
# "zero"          — no drift at all.
# "unconditional" — the symbol's own average log return over the fit window.
# "state"         — the shrunk PER-STATE conditional drift.
#
# `state` is a TIMING claim: "this indicator configuration predicts the next N
# bars." That is the family falsified across DT-0..3, EXP-37/39/45 on this
# platform, and the estimate is a max-of-32-states pick at t≈3 — about what pure
# noise produces. Keep it off by default.
#
# `unconditional` is NOT a timing claim. It is the asset's baseline drift — for
# equity indices, the equity risk premium, one of the most robust facts in
# finance. Setting it to zero is itself a claim, and PIT rejected it: SPY daily
# came back mean-PIT 0.619 with the middle quantiles 20pp light, the signature
# of a fan whose centre sits below where price actually goes (spec §6.5).
DriftMode = Literal["zero", "unconditional", "state"]


class ConditioningParams(BaseModel):
    """Indicator parameters defining the conditioning state.

    Fixed per interval — **never** optimized per symbol (spec D5). The
    reference charts carry a different tuned set on every image; that is a
    fitting-noise generator, not a model.
    """

    model_config = ConfigDict(frozen=True)

    psar_af_start: float = 0.02
    psar_af_max: float = 0.2
    tsi_long: int = 25
    tsi_short: int = 13
    tsi_signal: int = 13
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9


class PplParams(BaseModel):
    """Everything that determines a fan, so a snapshot is reproducible from it."""

    model_config = ConfigDict(frozen=True)

    interval: str = "30m"
    horizon_bars: int = Field(40, ge=1, le=400)
    n_paths: int = Field(2000, ge=100, le=50_000)
    n_render_paths: int = Field(300, ge=1, le=500)
    method: LevelMethod = "quantile"
    quantiles: tuple[float, float, float, float] = (0.15, 0.35, 0.65, 0.85)

    # Attach every simulated terminal price to the snapshot. The PIT harness
    # needs the FULL predictive CDF, not the thinned render subset: estimating
    # F(realized) from 500 drawn paths adds ~2pp of Monte Carlo noise to each
    # PIT value, which smears the distribution and biases the diagnosis toward
    # "the fan is too wide". Off for API responses — it would add n_paths
    # floats to every payload.
    include_terminal_sample: bool = False

    conditioning: ConditioningParams = ConditioningParams()

    # Restrict intraday bars to the ET regular session. Default True so the
    # live path shares its session geometry with the research tables the PIT
    # harness calibrates against — otherwise coverage measured on one says
    # nothing about the other. See `service.restrict_to_rth`.
    rth_only: bool = True

    # Estimation
    vol_model: VolModelKind = "gjr_fhs"
    drift_mode: DriftMode = "zero"
    fit_lookback_bars: int = Field(4000, ge=200)
    shrink_kappa: float = Field(200.0, ge=0.0)
    min_state_obs: int = Field(50, ge=1)
    min_move_sigma_mult: float = Field(0.25, ge=0.0)

    # `ewma_t` only. Ignored under `gjr_fhs`, which fits its own persistence
    # instead of assuming lambda and bootstraps innovations instead of fitting a
    # tail index.
    ewma_lambda: float = Field(0.94, gt=0.0, lt=1.0)
    nu: Optional[float] = None          # None → fit from residual kurtosis

    # Staleness: flag when the anchor bar is older than this many intervals.
    max_stale_bars: int = Field(3, ge=1)

    seed: Optional[int] = None          # None → derived, see service.py


class PplLevel(BaseModel):
    """One extracted price level.

    `side` and `rank` describe the level's position **in the forecast
    distribution**, not relative to spot: the two lower quantiles are "down",
    the two nearest the median are "inner". `pct_from_spot` carries the truth
    about where the level actually sits — under a strong drift a "down" level
    can print above spot, and that is information, not a bug.
    """

    model_config = ConfigDict(frozen=True)

    price: float
    quantile: Optional[float]           # None for density modes
    side: LevelSide
    rank: LevelRank
    pct_from_spot: float
    suppressed: bool = False
    suppressed_reason: Optional[str] = None


class PplState(BaseModel):
    """The conditioning state at the anchor bar, with its fit support."""

    model_config = ConfigDict(frozen=True)

    code: int                           # 0..31
    label: str                          # e.g. "psar_up|tsi_above_rising|macd_pos_rising"
    psar_side: Literal["above", "below"]
    psar_value: float
    tsi_value: float
    tsi_signal_value: float
    macd_hist: float
    observations: int                   # bars in this state across the fit window
    pooled: bool                        # True → fell back to the unconditional drift


class PplVolModel(BaseModel):
    """The fitted volatility dynamics behind one fan.

    Attached to every snapshot so the model is inspectable from its own output.
    The GARCH fields are None under `ewma_t`, which has no such parameters —
    that is the substantive difference between the two, not a missing value.
    """

    model_config = ConfigDict(frozen=True)

    kind: VolModelKind

    # Conditional sd for the FIRST forecast bar, deseasonalized. Under gjr_fhs
    # this already reflects the sign of the last observed move.
    sigma_next: float

    # ── GJR-GARCH(1,1), None under ewma_t ────────────────────────────
    omega: Optional[float] = None
    alpha: Optional[float] = None
    gamma: Optional[float] = None       # the leverage term; > 0 is the effect
    beta: Optional[float] = None

    # `alpha + gamma/2 + beta`. < 1 is what makes variance mean-revert; EWMA is
    # pinned at exactly 1, which is why its fan grew as sqrt(H) without bound.
    persistence: Optional[float] = None
    long_run_sigma: Optional[float] = None
    half_life_bars: Optional[float] = None
    # `(alpha+gamma)/alpha` — how much harder a down shock hits variance.
    # UNSTABLE when alpha is near zero, which is the norm for equity indices;
    # read it as a sign-and-magnitude indicator. Prefer the bounded field below
    # for display.
    leverage_ratio: Optional[float] = None
    # `(alpha+gamma)/(2*alpha+gamma)` — share of the average news impact coming
    # from down moves. Bounded [0,1]; 0.5 is symmetry. Below 0.5 means UP moves
    # drive volatility harder (real for gold).
    vol_response_asymmetry: Optional[float] = None
    # `var(H)/(H*var(1))` at this snapshot's horizon. 1.0 is the sqrt-of-time
    # assumption the old model was stuck with.
    variance_ratio: Optional[float] = None

    # ── FHS bootstrap pool, None under ewma_t ────────────────────────
    pool_size: Optional[int] = None
    pool_skew: Optional[float] = None
    pool_excess_kurtosis: Optional[float] = None

    # Did the GARCH filter actually remove the volatility clustering? These are
    # the lag-1 autocorrelation of |r| before and after filtering. The second
    # collapsing toward zero is the evidence that bootstrapping the residuals
    # as iid is legitimate — reported so the premise stays falsifiable.
    raw_abs_autocorr: Optional[float] = None
    residual_abs_autocorr: Optional[float] = None


class PplCalibration(BaseModel):
    """PIT coverage for this (symbol, interval). Populated in P3."""

    model_config = ConfigDict(frozen=True)

    window_start: datetime
    window_end: datetime
    n_effective: int
    coverage: dict[str, float]          # "0.15" → realized fraction below
    ks_stat: Optional[float] = None
    ks_p: Optional[float] = None
    computed_at: Optional[datetime] = None


class PplSnapshot(BaseModel):
    """One fan chart, fully self-describing."""

    symbol: str
    interval: str
    as_of: datetime                     # anchor bar timestamp (last COMPLETED bar)
    spot: float
    horizon_bars: int
    horizon_end: datetime

    levels: list[PplLevel]
    paths: list[list[float]]            # n_render_paths × (horizon_bars + 1)
    terminal_quantiles: dict[str, float]
    # All n_paths terminal prices — populated only when
    # `params.include_terminal_sample` is set (the PIT harness).
    terminal_sample: Optional[list[float]] = None

    sigma_horizon: float                # sd of terminal log return
    min_move: float                     # the "profit level" filter, as a fraction

    drift_mode: DriftMode
    drift_per_bar: float                # the drift ACTUALLY applied
    # Both candidate drifts, reported whatever the mode, so switching is
    # observable and each claim stays inspectable rather than silently discarded.
    state_drift_per_bar: float
    unconditional_drift_per_bar: float

    vol: PplVolModel

    # Tail index. Under `ewma_t` this is the Student-t parameter the sampler
    # actually drew from. Under `gjr_fhs` nothing is drawn from a Student-t —
    # it is a moment-matched **description** of the bootstrap pool's tails,
    # reported for continuity and comparison, not used to generate anything.
    nu: float
    bars_per_session: int

    state: PplState
    n_fit_bars: int

    # Staleness is tri-state on purpose. `staleness_checked=False` means the
    # check did not run — the market is closed (so an old bar is expected) or
    # the calendar was unavailable — and must NOT be read as "the feed is fine".
    stale: bool = False
    stale_age_bars: Optional[int] = None
    staleness_checked: bool = False

    calibration: Optional[PplCalibration] = None
    model_version: str = MODEL_VERSION
    seed: int
    params: PplParams


class PplResult(BaseModel):
    """Result object — predictable failures come back, they don't raise
    (`docs/standards/service_modules.md` rule 4)."""

    status: PplStatus
    snapshot: Optional[PplSnapshot] = None
    error: Optional[str] = None
    metadata: dict = Field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"
