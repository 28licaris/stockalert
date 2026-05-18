"""
Provider adjustment-probe framework — base types.

Defines the Protocol every provider's adjustment probe implements,
plus shared types for the probe spec, the per-probe result, and the
classifier that maps a returned close → "raw" / "split_adjusted" /
"other".

This is the substrate for `scripts/probe_provider_adjustment.py`.
Adding a new provider = drop a new file in this package implementing
the Protocol + register it. The runner picks it up automatically.

See `README.md` in this directory for the full onboarding checklist.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional, Protocol, runtime_checkable


# ─────────────────────────────────────────────────────────────────────
# Probe spec — what we're testing against
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ExpectedClose:
    """Ground-truth expected close prices on a single probe date."""
    raw: float                 # unadjusted close
    split_adjusted: float      # split-only adjusted close
    # Fully adjusted (split + cash dividends back-adjusted, à la Yahoo's
    # "Adj Close"). Many providers don't expose this; left optional.
    fully_adjusted: Optional[float] = None


@dataclass(frozen=True)
class ProbeSpec:
    """The known-historical-split event we use as our litmus test.

    Default is Apple's 4-for-1 split on 2020-08-31 — well-documented,
    mid-2020 (in reach of any provider with reasonable history), exact
    ratio known.

    Operators can override at the CLI for spot-checks against newer
    splits (e.g. NVDA's 10-for-1 on 2024-06-10) if Polygon's flat-file
    coverage doesn't yet reach 2020.
    """
    symbol: str
    pre_split_date: date       # the Friday-before-split (raw and adj differ here)
    post_split_date: date      # the Monday-of-split (raw == adj from here forward)
    split_factor: float        # e.g. 4.0 for a 4-for-1 forward split
    expected_pre: ExpectedClose
    expected_post: ExpectedClose


# ─────────────────────────────────────────────────────────────────────
# Known-split probe library
# ─────────────────────────────────────────────────────────────────────
#
# All registered providers receive the SAME ProbeSpec on a given run,
# so the comparison is apples-to-apples. Operator picks which split
# to test against via CLI flag (default below).
#
# When picking a probe, two constraints:
#   1. Every provider you care about must have data going back to
#      pre_split_date. Schwab pricehistory daily reaches multi-year;
#      Polygon REST + flat-files reach 2003+ (subscription-dependent).
#   2. The split must be UNCONTESTED — clean 4-for-1, 10-for-1, etc.,
#      not a confusing partial spinoff or reverse split. Picks below
#      are all clean forward splits.
#
# Expected closes are from Yahoo Finance's historical data table for
# each stock and verifiable independently. The raw close is the
# "Close" column; the split-adjusted is "Adj Close" (modulo dividend
# adjustments, which are tiny relative to a 4-for-1 split delta).
#
# **Adding a new known split:** add a new ProbeSpec below, plus a
# KNOWN_PROBES entry. Don't remove old ones — they're regression
# probes if a provider's behavior ever changes.

PROBE_AAPL_2020_4FOR1 = ProbeSpec(
    symbol="AAPL",
    pre_split_date=date(2020, 8, 28),
    post_split_date=date(2020, 8, 31),
    split_factor=4.0,
    expected_pre=ExpectedClose(
        raw=499.23,
        split_adjusted=499.23 / 4.0,        # ~124.81
    ),
    expected_post=ExpectedClose(
        raw=129.04,
        split_adjusted=129.04,
    ),
)

PROBE_NVDA_2024_10FOR1 = ProbeSpec(
    symbol="NVDA",
    pre_split_date=date(2024, 6, 7),       # Friday before split
    post_split_date=date(2024, 6, 10),     # Monday — split effective
    split_factor=10.0,
    expected_pre=ExpectedClose(
        raw=1208.88,
        split_adjusted=1208.88 / 10.0,      # ~120.89
    ),
    expected_post=ExpectedClose(
        raw=121.79,
        split_adjusted=121.79,
    ),
)

PROBE_AMZN_2022_20FOR1 = ProbeSpec(
    symbol="AMZN",
    pre_split_date=date(2022, 6, 3),       # Friday before split
    post_split_date=date(2022, 6, 6),      # Monday — split effective
    split_factor=20.0,
    expected_pre=ExpectedClose(
        raw=2447.00,
        split_adjusted=2447.00 / 20.0,      # ~122.35
    ),
    expected_post=ExpectedClose(
        raw=124.79,
        split_adjusted=124.79,
    ),
)

PROBE_GOOGL_2022_20FOR1 = ProbeSpec(
    symbol="GOOGL",
    pre_split_date=date(2022, 7, 15),       # Friday before split
    post_split_date=date(2022, 7, 18),      # Monday — split effective
    split_factor=20.0,
    expected_pre=ExpectedClose(
        raw=2255.34,
        split_adjusted=2255.34 / 20.0,       # ~112.77
    ),
    expected_post=ExpectedClose(
        raw=113.06,
        split_adjusted=113.06,
    ),
)

PROBE_TSLA_2022_3FOR1 = ProbeSpec(
    symbol="TSLA",
    pre_split_date=date(2022, 8, 24),        # Wednesday before split
    post_split_date=date(2022, 8, 25),       # Thursday — split effective
    split_factor=3.0,
    expected_pre=ExpectedClose(
        raw=891.29,
        split_adjusted=891.29 / 3.0,         # ~297.10
    ),
    expected_post=ExpectedClose(
        raw=296.07,
        split_adjusted=296.07,
    ),
)


# Operator-facing registry. Key these for CLI selection.
KNOWN_PROBES: dict[str, ProbeSpec] = {
    "aapl_2020_4for1": PROBE_AAPL_2020_4FOR1,
    "nvda_2024_10for1": PROBE_NVDA_2024_10FOR1,
    "amzn_2022_20for1": PROBE_AMZN_2022_20FOR1,
    "googl_2022_20for1": PROBE_GOOGL_2022_20FOR1,
    "tsla_2022_3for1": PROBE_TSLA_2022_3FOR1,
}

# Default — used when no `--probe` flag is passed. AAPL 2020 because
# it's old enough that every reasonable data window reaches it, and
# the 4-for-1 factor is large enough that raw vs adjusted is
# unambiguous to the eye (no 1.05 factors that could be confused
# with noise).
DEFAULT_PROBE_SPEC = PROBE_AAPL_2020_4FOR1


# ─────────────────────────────────────────────────────────────────────
# Probe result — what each probe returns per (provider, endpoint, date)
# ─────────────────────────────────────────────────────────────────────


# Tolerances — used by classify(). 50 cents OR 0.5% absolute,
# whichever is larger. Catches normal provider rounding differences
# without false-classifying.
TOLERANCE_DOLLARS = 0.50
TOLERANCE_PCT = 0.005


@dataclass
class ProbeResult:
    """One probe outcome: (provider, endpoint, probe_date) → close + verdict."""
    provider: str              # e.g. "polygon", "schwab"
    endpoint: str              # e.g. "polygon_rest_adjusted=false", "schwab_pricehistory"
    probe_date: date
    returned_close: Optional[float]
    classification: str        # see CLASSIFICATIONS below
    matches_raw: bool
    matches_split_adjusted: bool
    error: Optional[str] = None


CLASSIFICATIONS = frozenset({
    "raw",              # within tolerance of expected raw close
    "split_adjusted",   # within tolerance of expected split-adjusted close
    "other",            # returned a number but doesn't match either ground truth
    "no_data",          # endpoint succeeded but returned no row for this date
    "error",            # endpoint call raised; see `error` field
})


def close_to(value: Optional[float], expected: float) -> bool:
    """Within either absolute or relative tolerance of `expected`."""
    if value is None or expected == 0:
        return False
    abs_diff = abs(value - expected)
    pct_diff = abs_diff / abs(expected)
    return abs_diff <= TOLERANCE_DOLLARS or pct_diff <= TOLERANCE_PCT


def classify(value: Optional[float], expected: ExpectedClose) -> str:
    """Map a returned close → classification label.

    Order matters: check `raw` first; a fully-adjusted close is often
    very close to split-adjusted (in the absence of large historical
    dividends), so we don't need to differentiate at the probe level —
    `fully_adjusted` is informational.
    """
    if value is None:
        return "no_data"
    if close_to(value, expected.raw):
        return "raw"
    if close_to(value, expected.split_adjusted):
        return "split_adjusted"
    return "other"


# ─────────────────────────────────────────────────────────────────────
# The Protocol — every provider implements this
# ─────────────────────────────────────────────────────────────────────


@runtime_checkable
class ProviderAdjustmentProbe(Protocol):
    """Probe contract for one provider.

    A provider may expose multiple endpoints (e.g. Polygon has REST
    with adjusted=true/false plus flat-files via bronze). The probe
    returns one ProbeResult per (endpoint, probe_date) pair — so a
    provider with N endpoints × 2 dates produces 2N results.

    Implementations live in `app/services/silver/probes/<provider>.py`
    and self-register via `@register_probe("<name>")`.
    """

    provider_name: str

    async def probe(self, spec: ProbeSpec) -> list[ProbeResult]:
        """Run all of this provider's adjustment probes.

        Should NEVER raise; failure on one endpoint = a ProbeResult
        with classification="error". The runner is allowed to assume
        this so it can produce a complete report even when some
        providers are misconfigured.
        """
        ...
