"""Resolve a `RotationGroup` to a daily close series.

This is the seam that decouples the RRG math from how a group's price is
sourced. Phase 1 handles `kind="etf"` (single-symbol passthrough). The
`kind="basket"` branch is the Phase-2 extension point — it is defined and
tested now (it raises a clear error), never a silent stub.

Daily, split-adjusted closes come from ClickHouse — the fast single-symbol
hot tier — via `bars_gateway.get_chart_bars(..., source=AUTO, interval="1d")`,
which resamples `stocks.ohlcv_1m` server-side (`toStartOfInterval`, ET
trading day). RRG covers a fixed set of streamed symbols, so this is a
single-symbol-read problem, not a whole-market lake scan: CH holds the
history (one-time hot-load from the lake at setup; live stream keeps it
current going forward). AUTO self-heals — if CH lacks the window it
schedules a background lake→CH fill (windows ≤ 365d). The RRG lookback is
kept under that cap so the cache stays self-warming.
"""
from __future__ import annotations

import logging

import pandas as pd

from app.config import settings
from app.services.readers.bars_gateway import BarSource, get_chart_bars
from app.services.sectors.schemas import RotationGroup

logger = logging.getLogger(__name__)


class GroupResolutionError(RuntimeError):
    """A group's price series could not be built (no data, unknown kind…)."""


def _daily_close_series(symbol: str, lookback_days: int) -> pd.Series:
    """Date-indexed daily close series for one symbol from ClickHouse.

    Returns an empty Series (not an exception) when CH has no bars —
    callers decide whether that's fatal for the group.
    """
    bars = get_chart_bars(
        symbol,
        interval="1d",
        lookback_days=lookback_days,
        source=BarSource.AUTO,
    )
    if not bars:
        return pd.Series(dtype="float64")
    # Index by calendar date (RRG is a daily/weekly technique; intraday tz
    # nuance is irrelevant once we're on daily bars).
    idx = pd.to_datetime([b.timestamp for b in bars]).date
    s = pd.Series([b.close for b in bars], index=pd.Index(idx, name="date"), dtype="float64")
    # Guard against any duplicate trading days from the polygon∪schwab union.
    s = s[~s.index.duplicated(keep="last")].sort_index()
    return _despike(s, symbol)


def _despike(s: pd.Series, symbol: str, *, threshold: float = 0.4) -> pd.Series:
    """Neutralize single-bar reverting spikes (bad ticks) in a daily close
    series. A bad after-hours print can land as the ET-trading-day close and
    poison every relative-strength reading derived from it.

    A point is a spike when it deviates more than `threshold` from BOTH
    neighbours while the neighbours agree with each other (so a genuine
    gap/step — which does NOT revert — is preserved). Corrected points are
    replaced by the neighbour mean and LOGGED (no silent correction).
    Endpoints are left untouched.
    """
    if len(s) < 3:
        return s
    vals = s.to_numpy(dtype="float64").copy()
    fixed = 0
    for i in range(1, len(vals) - 1):
        a, b, c = vals[i - 1], vals[i], vals[i + 1]
        if a <= 0 or c <= 0:
            continue
        neigh = (a + c) / 2.0
        neighbors_agree = abs(a - c) / max(a, c) < threshold
        mid_deviates = abs(b - neigh) / neigh > threshold
        if neighbors_agree and mid_deviates:
            logger.warning(
                "resolver: despiked %s @ %s: %.2f -> %.2f (neighbours %.2f / %.2f)",
                symbol, s.index[i], b, neigh, a, c,
            )
            vals[i] = neigh
            fixed += 1
    if not fixed:
        return s
    logger.info("resolver: despiked %d bad bar(s) for %s", fixed, symbol)
    return pd.Series(vals, index=s.index, name=s.name)


def resolve(group: RotationGroup, *, lookback_days: int | None = None) -> pd.Series:
    """Return a date-indexed daily close series for `group`.

    Raises `GroupResolutionError` on an empty series or unknown kind.
    """
    lookback = lookback_days if lookback_days is not None else settings.rrg_lookback_days

    if group.kind == "etf":
        symbol = group.members[0]
        series = _daily_close_series(symbol, lookback)
        if series.empty:
            raise GroupResolutionError(
                f"no ClickHouse bars for ETF {symbol!r} over {lookback}d"
            )
        return series

    if group.kind == "basket":
        # Phase 2 seam: normalize each constituent to a base date, apply
        # `group.weights` (equal-weight when None), and sum into an index
        # series. Defined + tested now; implemented when the theme catalog
        # lands. Not a silent stub — it fails loudly if reached early.
        raise NotImplementedError(
            "basket resolution is a Phase-2 feature; "
            f"group {group.id!r} has kind='basket'. See docs/sector_rotation_spec.md."
        )

    raise GroupResolutionError(f"unknown group kind {group.kind!r} for {group.id!r}")
