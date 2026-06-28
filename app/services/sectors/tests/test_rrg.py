"""Unit tests for the RRG math — pure, no I/O."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.services.sectors import rrg


def _bdates(n: int) -> pd.Index:
    idx = pd.bdate_range("2024-01-01", periods=n)
    return pd.Index([d.date() for d in idx], name="date")


def _series(values, n=None) -> pd.Series:
    n = n if n is not None else len(values)
    return pd.Series(values, index=_bdates(n), dtype="float64")


# ── classify: the four quadrants + the 100/100 boundary ──────────────


@pytest.mark.parametrize(
    "ratio, mom, expected",
    [
        (105, 105, "leading"),
        (105, 95, "weakening"),
        (95, 105, "improving"),
        (95, 95, "lagging"),
        (100, 100, "leading"),   # documented inclusive boundary
        (100, 99.9, "weakening"),
        (99.9, 100, "improving"),
    ],
)
def test_classify_quadrants(ratio, mom, expected):
    assert rrg.classify(ratio, mom) == expected


# ── score: outperformer lands leading, underperformer lands lagging ──


def test_accelerating_outperformer_is_leading():
    # Super-exponential relative strength: outperforming AND the lead is still
    # widening, so rs_ratio > 100 and rs_momentum > 100 → leading. (A merely
    # linear lead decelerates in % terms — see the weakening case below.)
    n = 80
    bench = _series([100.0] * n)
    group = _series(list(100.0 * np.exp(0.0006 * np.arange(n) ** 2)))
    res = rrg.score(group, bench, ratio_window=5, mom_window=3, tail_weeks=4)
    assert res.sufficient
    assert res.current.quadrant == "leading"
    assert res.current.rs_ratio > 100
    assert res.current.rs_momentum > 100


def test_accelerating_underperformer_is_lagging():
    # Super-exponential decline: underperforming AND the gap is still widening,
    # so rs_ratio < 100 and rs_momentum < 100 → lagging.
    n = 80
    bench = _series([100.0] * n)
    group = _series(list(100.0 * np.exp(-0.0006 * np.arange(n) ** 2)))
    res = rrg.score(group, bench, ratio_window=5, mom_window=3, tail_weeks=4)
    assert res.sufficient
    assert res.current.quadrant == "lagging"
    assert res.current.rs_ratio < 100
    assert res.current.rs_momentum < 100


def test_decelerating_outperformer_is_weakening():
    # Linear (constant-slope) outperformance vs a flat benchmark: still ahead
    # (rs_ratio > 100) but the % lead shrinks over time (rs_momentum < 100).
    n = 80
    bench = _series([100.0] * n)
    group = _series(list(100.0 + np.arange(n) * 1.0))
    res = rrg.score(group, bench, ratio_window=5, mom_window=3, tail_weeks=4)
    assert res.sufficient
    assert res.current.quadrant == "weakening"


# ── totality: thin history returns a typed insufficient result ───────


def test_insufficient_history_is_typed_not_raised():
    n = 4  # fewer than ratio_window + mom_window
    bench = _series([100.0] * n)
    group = _series([101.0, 102.0, 103.0, 104.0])
    res = rrg.score(group, bench, ratio_window=5, mom_window=3, tail_weeks=4)
    assert not res.sufficient
    assert res.current is None
    assert "insufficient" in (res.reason or "")


def test_no_overlap_is_typed():
    bench = pd.Series(
        [100.0, 101.0],
        index=pd.Index([d.date() for d in pd.bdate_range("2020-01-01", periods=2)]),
    )
    group = pd.Series(
        [100.0, 101.0],
        index=pd.Index([d.date() for d in pd.bdate_range("2030-01-01", periods=2)]),
    )
    res = rrg.score(group, bench, ratio_window=5, mom_window=3, tail_weeks=4)
    assert not res.sufficient
    assert "overlap" in (res.reason or "")


# ── relative-strength line is rebased to 100 at the window start ─────


def test_relative_strength_rebased_to_100():
    n = 80
    bench = _series([100.0] * n)
    group = _series(list(100.0 + np.arange(n) * 0.5))
    res = rrg.score(group, bench, ratio_window=5, mom_window=3, tail_weeks=4)
    assert res.relative_strength[0][1] == pytest.approx(100.0)
    # rising group vs flat bench → ends above 100
    assert res.relative_strength[-1][1] > 100.0


# ── weekly tail is capped and ordered oldest → newest ────────────────


def test_weekly_tail_capped_and_ordered():
    n = 120
    bench = _series([100.0] * n)
    group = _series(list(100.0 + np.arange(n) * 0.3))
    res = rrg.score(group, bench, ratio_window=5, mom_window=3, tail_weeks=4)
    assert len(res.tail) <= 4
    dates = [p.date for p in res.tail]
    assert dates == sorted(dates)
