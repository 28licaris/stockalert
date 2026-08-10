"""Golden tests for Parabolic SAR (Wilder stop-and-reverse)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.indicators.psar import PSAR
from app.indicators.registry import get_indicator, list_indicators


def _series(vals):
    return pd.Series(vals, dtype="float64")


def _trend_side(close: pd.Series, sar: pd.Series) -> pd.Series:
    """+1 where the SAR sits below price (uptrend), -1 above (downtrend)."""
    return np.sign(close - sar)


# ─────────────────────────────────────────────────────────────────────
# Contract
# ─────────────────────────────────────────────────────────────────────


def test_psar_requires_high_low():
    with pytest.raises(ValueError):
        PSAR().compute(_series([1, 2, 3]))


@pytest.mark.parametrize(
    "kwargs",
    [
        {"af_start": 0.0},                    # non-positive acceleration
        {"af_start": -0.02},
        {"af_start": 0.05, "af_max": 0.02},   # cap below the start
        {"af_step": 0.0},                     # non-positive increment
    ],
)
def test_psar_rejects_bad_params(kwargs):
    with pytest.raises(ValueError):
        PSAR(**kwargs)


def test_psar_is_registered():
    assert "psar" in list_indicators()
    ind = get_indicator("psar", af_start=0.028, af_max=0.214)
    assert isinstance(ind, PSAR)
    assert ind.af_start == 0.028 and ind.af_max == 0.214
    # The acceleration doubles as the per-EP increment unless overridden.
    assert ind.af_step == 0.028


def test_psar_series_shorter_than_warmup_is_all_nan():
    close = _series([10, 11])
    sar = PSAR().compute(close, close * 1.01, close * 0.99)
    assert len(sar) == 2
    assert sar.isna().all()


def test_psar_warmup_bars_are_nan():
    n = 40
    close = _series([100 + i for i in range(n)])
    sar = PSAR().compute(close, close * 1.01, close * 0.99)
    assert sar.iloc[:2].isna().all(), "seed bars must not be reported as readings"
    assert sar.iloc[2:].notna().all()


# ─────────────────────────────────────────────────────────────────────
# Golden values — hand-computed from Wilder's recursion
# ─────────────────────────────────────────────────────────────────────


def test_psar_matches_hand_computed_recursion():
    """Six bars worked through by hand with af_start=0.02, af_max=0.2.

    Seed: close[1] >= close[0] so up=True, sar[1]=low[0]=9, af=0.02,
    ep_high=max(high[0], high[1])=11, ep_low=min(low[0], low[1])=9.

    i=2  sar = 9.00 + 0.02*(11 - 9.00)      = 9.04   → no flip (low 11 >= 9.04)
         new EP high 12, af→0.04
         clamp to min(9.04, low[1]=10, low[0]=9)    = 9.00
    i=3  sar = 9.00 + 0.04*(12 - 9.00)      = 9.12   → no flip
         new EP high 13, af→0.06
         clamp to min(9.12, low[2]=11, low[1]=10)   = 9.12
    i=4  sar = 9.12 + 0.06*(13 - 9.12)      = 9.3528 → low 8 < 9.3528, FLIP
         sar := prior EP high                        = 13.00, ep_low=8, af=0.02
    i=5  sar = 13.00 - 0.02*(13.00 - 8)     = 12.90  → no flip (high 9 <= 12.90)
         new EP low 7, af→0.04
         clamp to max(12.90, high[4]=13.5, high[3]=13) = 13.50
    """
    high = _series([10.0, 11.0, 12.0, 13.0, 13.5, 9.0])
    low = _series([9.0, 10.0, 11.0, 12.0, 8.0, 7.0])
    close = _series([9.5, 10.5, 11.5, 12.5, 8.5, 7.5])

    sar = PSAR(af_start=0.02, af_max=0.2).compute(close, high, low)

    assert sar.iloc[:2].isna().all()
    np.testing.assert_allclose(
        sar.iloc[2:].to_numpy(),
        [9.00, 9.12, 13.00, 13.50],
        rtol=0,
        atol=1e-12,
    )


# ─────────────────────────────────────────────────────────────────────
# Wilder's invariants
# ─────────────────────────────────────────────────────────────────────


def test_psar_trails_below_price_in_a_clean_uptrend():
    n = 120
    close = _series([100.0 + i for i in range(n)])
    high, low = close * 1.01, close * 0.99
    sar = PSAR().compute(close, high, low).dropna()

    assert (sar < close.loc[sar.index]).all(), "SAR must sit below price in an uptrend"
    assert (sar.diff().dropna() >= 0).all(), "SAR must trail monotonically in a clean trend"


def test_psar_trails_above_price_in_a_clean_downtrend():
    n = 120
    close = _series([100.0 + (n - i) for i in range(n)])
    high, low = close * 1.01, close * 0.99
    sar = PSAR().compute(close, high, low).dropna()
    # The seed reads its direction from the first two closes, so a clean
    # downtrend is short from the first computed bar — no settling period.
    assert (sar > close.loc[sar.index]).all(), "SAR must sit above price in a downtrend"


def test_psar_never_penetrates_the_prior_two_lows_in_an_uptrend():
    """Wilder's second constraint — without it the level lands inside the
    current bar and manufactures phantom flips."""
    n = 120
    close = _series([100.0 + i for i in range(n)])
    high, low = close * 1.01, close * 0.99
    sar = PSAR().compute(close, high, low)

    # A clean monotonic rise never reverses, so the constraint binds on every bar.
    for i in range(2, n):
        assert sar.iloc[i] <= min(low.iloc[i - 1], low.iloc[i - 2]) + 1e-12, (
            f"bar {i}: SAR {sar.iloc[i]:.4f} penetrated prior lows "
            f"({low.iloc[i - 1]:.4f}, {low.iloc[i - 2]:.4f})"
        )


def test_psar_flips_on_a_v_reversal():
    up = [100.0 + i for i in range(40)]
    down = [up[-1] - 2.0 * i for i in range(1, 41)]
    close = _series(up + down)
    high, low = close * 1.01, close * 0.99
    sar = PSAR().compute(close, high, low)

    side = _trend_side(close, sar).dropna()
    assert (side.iloc[:35] > 0).all(), "should be long through the rise"
    assert side.iloc[-1] < 0, "should have flipped short by the end of the fall"
    assert (side.diff().dropna() != 0).any(), "a reversal must actually occur"


# ─────────────────────────────────────────────────────────────────────
# Acceleration
# ─────────────────────────────────────────────────────────────────────


def test_af_cap_changes_the_result_and_a_pinned_af_is_valid():
    """af_max == af_start pins the accelerator; it must stay a valid SAR and
    must not agree with an accelerating configuration."""
    rng = np.random.default_rng(20260806)
    steps = rng.normal(0.15, 1.0, 300).cumsum()
    close = _series(100.0 + steps)
    high, low = close * 1.01, close * 0.99

    pinned = PSAR(af_start=0.02, af_max=0.02).compute(close, high, low)
    accel = PSAR(af_start=0.02, af_max=0.2).compute(close, high, low)

    assert pinned.notna().iloc[2:].all() and accel.notna().iloc[2:].all()
    assert not np.allclose(pinned.iloc[2:], accel.iloc[2:]), (
        "capping the acceleration factor must change the trailing level"
    )


def test_faster_acceleration_flips_more_often_on_noisy_data():
    rng = np.random.default_rng(11)
    close = _series(100.0 + rng.normal(0, 1.0, 500).cumsum())
    high, low = close * 1.005, close * 0.995

    def flips(af_max: float) -> int:
        sar = PSAR(af_start=0.02, af_max=af_max).compute(close, high, low)
        side = _trend_side(close, sar).dropna()
        return int((side.diff().dropna() != 0).sum())

    slow, fast = flips(0.05), flips(0.5)
    assert fast > slow, (
        f"a faster accelerator should whipsaw more: af_max=0.5 gave {fast} flips, "
        f"af_max=0.05 gave {slow}"
    )


def test_psar_is_causal():
    """The level at bar t must not change when future bars are appended."""
    rng = np.random.default_rng(7)
    close = _series(100.0 + rng.normal(0, 1.0, 200).cumsum())
    high, low = close * 1.01, close * 0.99

    full = PSAR().compute(close, high, low)
    truncated = PSAR().compute(close.iloc[:150], high.iloc[:150], low.iloc[:150])

    np.testing.assert_allclose(
        full.iloc[2:150].to_numpy(), truncated.iloc[2:].to_numpy(), rtol=0, atol=1e-12
    )
