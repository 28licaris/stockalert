"""Bar-permutation kernel (MCPT) + significance machinery tests."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass

import numpy as np
import pandas as pd
import pytest

from app.services.sim.permutation import (
    PermutedBar,
    noise_frames,
    permute_bar_lists,
    permute_frames,
)
from app.services.sim.schemas import Bar
from app.services.sim.significance import benjamini_hochberg, mcpt_pvalue

UTC = dt.timezone.utc
T0 = dt.datetime(2024, 1, 1, tzinfo=UTC)


def _walk_frame(n=250, seed=7, drift=0.0005, vol=0.02, start=T0, shock=None):
    """Synthetic OHLCV random-walk frame with valid bar geometry."""
    rng = np.random.default_rng(seed)
    rets = drift + vol * rng.standard_normal(n) + (shock if shock is not None else 0.0)
    close = 100.0 * np.exp(np.cumsum(rets))
    open_ = np.empty(n)
    open_[0] = 100.0
    open_[1:] = close[:-1] * np.exp(vol * 0.25 * rng.standard_normal(n - 1))
    hi = np.maximum(open_, close) * np.exp(np.abs(vol * 0.5 * rng.standard_normal(n)))
    lo = np.minimum(open_, close) * np.exp(-np.abs(vol * 0.5 * rng.standard_normal(n)))
    volume = rng.integers(1_000, 100_000, n).astype(float)
    idx = pd.DatetimeIndex([start + dt.timedelta(days=i) for i in range(n)])
    return pd.DataFrame(
        {"open": open_, "high": hi, "low": lo, "close": close, "volume": volume}, index=idx
    )


def _bodies(df):
    """Sorted multiset of (r_h, r_l, r_c, volume) bar bodies from index 1 on."""
    lp = np.log(df[["open", "high", "low", "close"]].to_numpy())
    body = np.column_stack(
        [lp[1:, 1] - lp[1:, 0], lp[1:, 2] - lp[1:, 0], lp[1:, 3] - lp[1:, 0],
         df["volume"].to_numpy()[1:]]
    )
    return np.array(sorted(map(tuple, np.round(body, 12))))


def _gaps(df):
    lp = np.log(df[["open", "close"]].to_numpy())
    return np.sort(np.round(lp[1:, 0] - lp[:-1, 1], 12))


# ── kernel ───────────────────────────────────────────────────────────


def test_deterministic_by_seed():
    real = {"A": _walk_frame()}
    p1 = permute_frames(real, seed=42)["A"]
    p2 = permute_frames(real, seed=42)["A"]
    p3 = permute_frames(real, seed=43)["A"]
    pd.testing.assert_frame_equal(p1, p2)
    assert not p1["close"].equals(p3["close"])
    assert not p1["close"].equals(real["A"]["close"])  # actually shuffled


def test_multiset_and_terminal_price_preserved():
    real = _walk_frame()
    perm = permute_frames({"A": real}, seed=1)["A"]
    np.testing.assert_allclose(_bodies(perm), _bodies(real), atol=1e-9)
    np.testing.assert_allclose(_gaps(perm), _gaps(real), atol=1e-9)
    # Same gap+body multisets => same terminal close and same return moments.
    assert perm["close"].iloc[-1] == pytest.approx(real["close"].iloc[-1])
    r_real = np.diff(np.log(real["close"]))
    r_perm = np.diff(np.log(perm["close"]))
    assert r_perm.mean() == pytest.approx(r_real.mean(), abs=1e-4)
    assert r_perm.std() == pytest.approx(r_real.std(), rel=0.15)


def test_ohlc_validity_preserved():
    perm = permute_frames({"A": _walk_frame(seed=3)}, seed=2)["A"]
    assert (perm["high"] >= np.maximum(perm["open"], perm["close"]) - 1e-12).all()
    assert (perm["low"] <= np.minimum(perm["open"], perm["close"]) + 1e-12).all()
    assert (perm[["open", "high", "low", "close"]] > 0).all().all()


def test_prefix_real_after_start_after():
    real = _walk_frame(n=300)
    cut = T0 + dt.timedelta(days=99)  # first 100 bars real
    perm = permute_frames({"A": real}, seed=5, start_after=cut)["A"]
    pd.testing.assert_frame_equal(perm.iloc[:100], real.iloc[:100])
    assert not perm["close"].iloc[100:].equals(real["close"].iloc[100:])
    # anchor continuity: permuted section still chains off the real prefix
    assert perm["close"].iloc[-1] == pytest.approx(real["close"].iloc[-1])


def test_shared_shuffle_preserves_cross_correlation():
    rng = np.random.default_rng(11)
    common = 0.02 * rng.standard_normal(400)
    a = _walk_frame(n=400, seed=1, vol=0.005, shock=common)
    b = _walk_frame(n=400, seed=2, vol=0.005, shock=common)
    r = permute_frames({"A": a, "B": b}, seed=9)
    corr_real = np.corrcoef(np.diff(np.log(a["close"])), np.diff(np.log(b["close"])))[0, 1]
    corr_perm = np.corrcoef(np.diff(np.log(r["A"]["close"])), np.diff(np.log(r["B"]["close"])))[0, 1]
    assert corr_real > 0.8  # sanity: construction worked
    assert abs(corr_perm - corr_real) < 0.15


def test_identical_symbols_get_identical_shuffle():
    df = _walk_frame(seed=21)
    r = permute_frames({"A": df, "B": df.copy()}, seed=13)
    pd.testing.assert_frame_equal(r["A"], r["B"])


def test_partial_coverage_symbol():
    a = _walk_frame(n=300, seed=1)
    b = _walk_frame(n=150, seed=2, start=T0 + dt.timedelta(days=150))  # late IPO
    r = permute_frames({"A": a, "B": b}, seed=4)
    assert list(r["B"].index) == list(b.index)  # calendar untouched
    np.testing.assert_allclose(_bodies(r["B"]), _bodies(b), atol=1e-9)
    np.testing.assert_allclose(_gaps(r["B"]), _gaps(b), atol=1e-9)
    assert r["B"]["close"].iloc[-1] == pytest.approx(b["close"].iloc[-1])


def test_validation_rejects_bad_input():
    good = _walk_frame(n=50)
    with pytest.raises(ValueError, match="no symbols"):
        permute_frames({}, seed=1)
    bad = good.copy()
    bad.iloc[10, bad.columns.get_loc("low")] = -1.0
    with pytest.raises(ValueError, match="non-positive"):
        permute_frames({"A": bad}, seed=1)
    with pytest.raises(ValueError, match="missing columns"):
        permute_frames({"A": good.drop(columns=["high"])}, seed=1)
    with pytest.raises(ValueError, match="nothing to shuffle"):
        permute_frames({"A": good}, seed=1, start_after=good.index[-1])


# ── noise test kernel ────────────────────────────────────────────────


def test_noise_preserves_sequence_but_moves_prices():
    real = _walk_frame(n=300, seed=17)
    noisy = noise_frames({"A": real}, seed=3, scale=0.25)["A"]
    r_real = np.diff(np.log(real["close"]))
    r_noise = np.diff(np.log(noisy["close"]))
    corr = np.corrcoef(r_real, r_noise)[0, 1]
    assert corr > 0.9  # the SEQUENCE survives (unlike a permutation)
    assert not np.allclose(noisy["close"], real["close"])  # but prices moved
    # bar 0 anchors; volume untouched
    assert noisy.iloc[0][["open", "high", "low", "close"]].tolist() == pytest.approx(
        real.iloc[0][["open", "high", "low", "close"]].tolist())
    np.testing.assert_array_equal(noisy["volume"], real["volume"])


def test_noise_keeps_ohlc_valid_and_deterministic():
    real = _walk_frame(n=200, seed=23)
    n1 = noise_frames({"A": real}, seed=9)["A"]
    n2 = noise_frames({"A": real}, seed=9)["A"]
    pd.testing.assert_frame_equal(n1, n2)
    assert (n1["high"] >= np.maximum(n1["open"], n1["close"]) - 1e-12).all()
    assert (n1["low"] <= np.minimum(n1["open"], n1["close"]) + 1e-12).all()
    with pytest.raises(ValueError, match="scale"):
        noise_frames({"A": real}, seed=1, scale=1.5)


# ── Bar-list adapter ─────────────────────────────────────────────────


@dataclass
class _Bar:
    symbol: str
    timestamp: dt.datetime
    open: float
    high: float
    low: float
    close: float
    volume: float = 1000.0


def test_bar_list_adapter_roundtrip():
    df = _walk_frame(n=60)
    bars = [
        _Bar("A", ts.to_pydatetime(), r.open, r.high, r.low, r.close, r.volume)
        for ts, r in df.iterrows()
    ]
    out = permute_bar_lists({"A": bars, "EMPTY": []}, seed=3)
    assert out["EMPTY"] == []
    assert len(out["A"]) == 60
    pb = out["A"][0]
    assert isinstance(pb, PermutedBar) and isinstance(pb, Bar)
    assert pb.timestamp == bars[0].timestamp  # calendar preserved
    assert out["A"][0].close == pytest.approx(bars[0].close)  # anchor bar real


# ── significance ─────────────────────────────────────────────────────


def test_mcpt_pvalue_counts():
    res = mcpt_pvalue(2.0, [1.0] * 90 + [2.5] * 9, greater_is_better=True)
    assert res.n_as_good == 9
    assert res.p_value == pytest.approx(10 / 100)
    lower = mcpt_pvalue(1.0, [0.5, 2.0, 3.0], greater_is_better=False)
    assert lower.n_as_good == 1 and lower.p_value == pytest.approx(2 / 4)
    with pytest.raises(ValueError, match="empty"):
        mcpt_pvalue(1.0, [])
    assert "p=" in res.summary()


def test_benjamini_hochberg_known_example():
    p = [0.01, 0.04, 0.03, 0.005]
    q = benjamini_hochberg(p)
    # sorted p: .005, .01, .03, .04 -> raw q: .02, .02, .04, .04
    assert q == pytest.approx([0.02, 0.04, 0.04, 0.02])
    with pytest.raises(ValueError, match="no p-values"):
        benjamini_hochberg([])
    with pytest.raises(ValueError, match="outside"):
        benjamini_hochberg([0.5, 1.2])


# ── session-aware kernel (intraday) ──────────────────────────────────


def _hourly_frame(days=120, seed=5, start=T0):
    """Synthetic 7-bar-per-day session frames with hour-of-day vol structure."""
    rng = np.random.default_rng(seed)
    rows, ts, px = [], [], 100.0
    vol_by_bar = [0.006, 0.003, 0.002, 0.002, 0.002, 0.003, 0.005]  # U-shape
    d = start
    for _ in range(days):
        if d.weekday() < 5:
            for h, v in zip(range(7), vol_by_bar):
                px *= float(np.exp(v * rng.standard_normal()))
                t = d.replace(hour=9 + h, minute=30)
                ts.append(t)
                rows.append((px * 0.999, px * 1.001, px * 0.998, px))
        d += dt.timedelta(days=1)
    a = np.array(rows)
    return pd.DataFrame({"open": a[:, 0], "high": a[:, 1], "low": a[:, 2],
                         "close": a[:, 3], "volume": 1000.0},
                        index=pd.DatetimeIndex(ts))


def test_session_aware_preserves_time_of_day_pools():
    real = _hourly_frame()
    perm = permute_frames({"A": real}, seed=7, session_aware=True)["A"]
    lp_r = np.log(real[["open", "high", "low", "close"]].to_numpy())
    lp_p = np.log(perm[["open", "high", "low", "close"]].to_numpy())
    body_r = lp_r[:, 3] - lp_r[:, 0]
    body_p = lp_p[:, 3] - lp_p[:, 0]
    hours = np.array([t.hour for t in real.index])
    for h in np.unique(hours):
        m = hours == h  # bodies shuffled only WITHIN each hour pool
        np.testing.assert_allclose(np.sort(body_p[m]), np.sort(body_r[m]), atol=1e-12)
    # overnight gaps stay a closed pool: multiset of first-bar gaps preserved
    dates = np.array([t.date() for t in real.index])
    overnight = np.ones(len(dates), dtype=bool)
    overnight[1:] = dates[1:] != dates[:-1]
    gap_r = lp_r[:, 0] - np.roll(lp_r[:, 3], 1)
    gap_p = lp_p[:, 0] - np.roll(lp_p[:, 3], 1)
    np.testing.assert_allclose(
        np.sort(gap_p[overnight][1:]), np.sort(gap_r[overnight][1:]), atol=1e-12)
    # and the sequence actually changed
    assert not np.allclose(perm["close"], real["close"])


def test_session_aware_requires_alignment():
    a = _hourly_frame(days=60)
    b = _hourly_frame(days=30, start=T0 + dt.timedelta(days=60))
    with pytest.raises(ValueError, match="aligned"):
        permute_frames({"A": a, "B": b}, seed=1, session_aware=True)


def test_session_aware_default_off_unchanged():
    real = {"A": _walk_frame()}
    p_default = permute_frames(real, seed=42)["A"]
    p_explicit = permute_frames(real, seed=42, session_aware=False)["A"]
    pd.testing.assert_frame_equal(p_default, p_explicit)
