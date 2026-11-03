"""Vol Premium screen — public DTOs.

The only files another service imports are this one and `contract.py`.
Spec: `docs/vol_premium_screen_spec.md`.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Bump when a change invalidates a stored screen row.
MODEL_VERSION = "volprem-0.1.0"

# US equity trading days per year, and the calendar days they span. Used to
# convert an option's calendar days-to-expiry into the horizon the volatility
# forecast is actually defined over. Skipping this conversion inflates the
# forecast by ~17% and manufactures premium on every row.
TRADING_DAYS_PER_YEAR = 252
CALENDAR_DAYS_PER_YEAR = 365

RowStatus = Literal["ok", "no_chain", "no_forecast", "stale"]


class ScreenParams(BaseModel):
    """Everything that determines a screen, so a row is reproducible from it."""

    model_config = ConfigDict(frozen=True)

    # ── expiry selection ─────────────────────────────────────────────
    # 30 days is the VIX convention and the most liquid part of the curve.
    target_dte: int = Field(30, ge=1)
    min_dte: int = Field(14, ge=1)
    max_dte: int = Field(60, ge=1)

    # ── at-the-money band ────────────────────────────────────────────
    # Single-contract IV is noisy and skew makes one lone call or put
    # unrepresentative, so IV is averaged over a narrow band around spot.
    atm_band: float = Field(0.05, gt=0.0, le=0.5)
    min_contracts: int = Field(4, ge=2)

    # ── liquidity and sanity ─────────────────────────────────────────
    # Schwab uses -999 to mean "no value", and thinly-traded strikes make the
    # IV solver emit values in the hundreds. Both are excluded, not repaired.
    iv_min: float = Field(1.0, gt=0.0)
    iv_max: float = Field(300.0, gt=0.0)
    min_open_interest: int = Field(10, ge=0)
    max_rel_spread: float = Field(0.25, gt=0.0)

    # A chain older than this is not a picture of today's market.
    freshness_hours: int = Field(36, ge=1)

    # Bars behind the volatility forecast. Matches PPL's window so the two
    # surfaces cannot disagree about the same symbol.
    fit_lookback_bars: int = Field(4000, ge=600)


class AtmImpliedVol(BaseModel):
    """The implied-volatility half of one row, with its own support."""

    model_config = ConfigDict(frozen=True)

    expiration_date: date
    days_to_expiration: int
    implied_vol: float                  # percent, e.g. 24.5
    contracts_used: int
    calls: int
    puts: int
    median_rel_spread: float
    snapshot_ts: datetime


class VolPremiumRow(BaseModel):
    """One symbol's answer to "are its options expensive right now?"."""

    symbol: str
    as_of: datetime
    spot: float

    expiration_date: date
    days_to_expiration: int             # calendar, as the chain reports it
    trading_days: int                   # what the forecast is defined over

    # Both in percent, annualized, and directly comparable — that comparability
    # is the entire product and §3.2 of the spec is why it holds.
    implied_vol: float
    forecast_vol: float

    # The ranking column. Scale-free, so one sort orders the whole universe.
    vrp_ratio: float
    # The sizing column: the gap in volatility points.
    vrp_points: float

    # Where today's ratio sits in THIS symbol's own history, 0-100.
    #
    # The headline number. A positive premium is normal, so a bare ratio cannot
    # say whether today is unusual; only the symbol's own distribution can.
    # None means no baseline exists for this symbol — which is a different
    # claim from "unremarkable" and must never be rendered as 50.
    premium_percentile: Optional[float] = None
    baseline_days: int = 0

    # ── quality, shown on the page (spec D7) ─────────────────────────
    contracts_used: int
    calls: int
    puts: int
    median_rel_spread: float
    snapshot_age_hours: float

    # ── forecast provenance, so a ratio can be audited to its fit ────
    garch_sigma_next: float
    garch_persistence: float
    variance_ratio: float
    fit_bars: int

    model_version: str = MODEL_VERSION


class SkippedSymbol(BaseModel):
    """A symbol that produced no row, and why.

    Present so a short screen is visibly short rather than quietly so — a
    missing symbol must never look like a symbol with no premium.
    """

    model_config = ConfigDict(frozen=True)

    symbol: str
    status: RowStatus
    reason: str


class VolPremiumScreen(BaseModel):
    """The full ranked table plus everything needed to read it honestly."""

    as_of: datetime
    rows: list[VolPremiumRow]
    skipped: list[SkippedSymbol] = Field(default_factory=list)
    params: ScreenParams = ScreenParams()
    model_version: str = MODEL_VERSION

    @property
    def universe_size(self) -> int:
        return len(self.rows) + len(self.skipped)


class VolPremiumRowResult(BaseModel):
    """Result object — predictable failures come back, they do not raise
    (`docs/standards/service_modules.md` rule 4)."""

    status: RowStatus
    row: Optional[VolPremiumRow] = None
    reason: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status == "ok"
