"""Resolve a `RotationGroup` to a daily close series.

This is the seam that decouples the RRG math from how a group's price is
sourced. `kind="etf"` is a single-symbol passthrough; `kind="basket"` is a
weighted index of its constituents.

Daily, split-adjusted closes come from ClickHouse `ohlcv_daily` — the **daily**
table, read in ONE query for every symbol the dashboard needs
(:func:`load_daily_closes`). RRG is a daily/weekly technique, so it has no use for
minute bars, and reading them was both slow (about 60 one-symbol reads per page
load) and stale for any symbol past the 300-symbol Schwab streaming cap
(`docs/ISSUES.md#schwab-stream-300-symbol-account-cap`). There is deliberately NO
fallback to the minute path: a symbol without daily data is excluded with a named
reason rather than silently served stale.
"""
from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from datetime import date, timedelta

import pandas as pd

from app.services.sectors.schemas import RotationGroup

logger = logging.getLogger(__name__)


class GroupResolutionError(RuntimeError):
    """A group's price series could not be built (no data, unknown kind…)."""


def load_daily_closes(symbols: Sequence[str], lookback_days: int, *, client=None) -> dict[str, pd.Series]:
    """Date-indexed daily close series for every symbol in ONE ClickHouse query.

    Symbols with no rows in the window are simply absent from the result (callers
    decide whether that is fatal for a group). Each series is de-spiked.
    """
    syms = sorted({s for s in symbols if s})
    if not syms:
        return {}
    if client is None:
        from app.db.client import get_client

        client = get_client()
    since = date.today() - timedelta(days=lookback_days)
    rows = client.query(
        "SELECT symbol, toDate(timestamp) AS d, close FROM ohlcv_daily FINAL "
        "WHERE symbol IN {syms:Array(String)} AND toDate(timestamp) >= {since:Date} "
        "ORDER BY symbol, d",
        parameters={"syms": syms, "since": since},
    ).result_rows
    by_sym: dict[str, tuple[list, list]] = {}
    for sym, d, close in rows:
        idx, vals = by_sym.setdefault(sym, ([], []))
        idx.append(d)
        vals.append(float(close))
    out: dict[str, pd.Series] = {}
    for sym, (idx, vals) in by_sym.items():
        ser = pd.Series(vals, index=pd.Index(idx, name="date"), dtype="float64")
        ser = ser[~ser.index.duplicated(keep="last")].sort_index()
        out[sym] = _despike(ser, sym)
    return out


def _series(closes: Mapping[str, pd.Series], symbol: str) -> pd.Series:
    s = closes.get(symbol)
    return s if s is not None else pd.Series(dtype="float64")


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


def resolve(group: RotationGroup, closes: Mapping[str, pd.Series]) -> pd.Series:
    """Return a date-indexed daily close series for `group`, from the pre-loaded
    ``closes`` (see :func:`load_daily_closes`).

    Raises `GroupResolutionError` on an empty series or unknown kind.
    """
    if group.kind == "etf":
        symbol = group.members[0]
        series = _series(closes, symbol)
        if series.empty:
            raise GroupResolutionError(f"no daily data for {symbol}")
        return series

    if group.kind == "basket":
        return _basket_close_series(group, closes)

    raise GroupResolutionError(f"unknown group kind {group.kind!r} for {group.id!r}")


def _normalize_weights(group: RotationGroup, available: list[str]) -> dict[str, float]:
    """Per-member weights summing to 1 over the members that resolved.
    Uses `group.weights` when given (renormalized over what's available),
    else equal weight."""
    if group.weights:
        w = {s: float(group.weights.get(s, 0.0)) for s in available}
        total = sum(w.values())
        if total > 0:
            return {s: v / total for s, v in w.items()}
    n = len(available)
    return {s: 1.0 / n for s in available}


def _basket_close_series(group: RotationGroup, closes: Mapping[str, pd.Series]) -> pd.Series:
    """Composite index for a basket: normalize each constituent's daily
    close to its window-start value, then take the weighted average. The
    index level is arbitrary (RS-Ratio divides it out); only the *shape*
    vs the benchmark matters.

    Members that have no daily data are dropped (logged) rather than
    failing the whole basket. Dates are the intersection across resolved
    members, so the composite has no gaps from a single late lister.
    """
    series_by_sym: dict[str, pd.Series] = {}
    missing: list[str] = []
    for sym in group.members:
        s = _series(closes, sym)
        if s.empty:
            missing.append(sym)
        else:
            series_by_sym[sym] = s

    if not series_by_sym:
        raise GroupResolutionError(
            f"basket {group.id!r}: no daily data for any of {len(group.members)} members"
        )

    df = pd.DataFrame(series_by_sym).sort_index().dropna()
    if len(df) < 2:
        raise GroupResolutionError(
            f"basket {group.id!r}: <2 common dates across members "
            f"({len(series_by_sym)} resolved)"
        )

    weights = _normalize_weights(group, list(df.columns))
    # Rebase each member to 1.0 at the first common date, weight, sum → ×100.
    norm = df.divide(df.iloc[0])
    composite = norm.mul(pd.Series(weights)).sum(axis=1) * 100.0

    if missing:
        logger.info(
            "basket %s: %d/%d members resolved (dropped: %s)",
            group.id, len(series_by_sym), len(group.members), ", ".join(missing),
        )
    return composite
