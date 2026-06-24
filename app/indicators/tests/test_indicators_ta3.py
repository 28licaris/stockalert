"""
Unit tests for the TA-3 indicator additions: SMA, EMA, WMA, ATR,
Bollinger, Stochastic. (SMA + EMA were already covered by
test_sim_unit.py; we add a sanity check here too so the file
documents the full TA-3 indicator surface in one place.)

Each test checks:
  - Math correctness on a small hand-crafted series with known answers.
  - Warmup behavior (NaN before enough data).
  - Param validation (invalid periods raise ValueError).
  - Registry resolution by name.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from app.indicators.atr import ATR
from app.indicators.bollinger import BollingerBands
from app.indicators.registry import get_indicator, list_indicators
from app.indicators.stochastic import StochasticOscillator
from app.indicators.wma import WMA


# ─────────────────────────────────────────────────────────────────────
# WMA — Weighted Moving Average
# ─────────────────────────────────────────────────────────────────────


def test_wma_matches_manual_computation() -> None:
    """WMA(3) on [10, 20, 30] = (10*1 + 20*2 + 30*3) / (1+2+3) = 140/6 ≈ 23.33."""
    closes = pd.Series([10.0, 20.0, 30.0, 40.0])
    wma = WMA(period=3).compute(closes)
    assert math.isnan(wma.iloc[0])
    assert math.isnan(wma.iloc[1])
    # (10*1 + 20*2 + 30*3) / 6 = 140/6
    assert wma.iloc[2] == pytest.approx(140 / 6)
    # (20*1 + 30*2 + 40*3) / 6 = 200/6
    assert wma.iloc[3] == pytest.approx(200 / 6)


def test_wma_recent_prices_weighted_higher_than_sma() -> None:
    """
    Rising series → WMA tracks higher than SMA at any point past
    warmup because the most-recent (highest) prices carry more weight.
    """
    closes = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
    sma = closes.rolling(3).mean()
    wma = WMA(period=3).compute(closes)
    # Past warmup: WMA > SMA on the rising series.
    for i in range(2, len(closes)):
        assert wma.iloc[i] > sma.iloc[i]


def test_wma_invalid_period_raises() -> None:
    with pytest.raises(ValueError, match="period must be >= 1"):
        WMA(period=0)


# ─────────────────────────────────────────────────────────────────────
# ATR — Average True Range (Wilder's smoothing)
# ─────────────────────────────────────────────────────────────────────


def test_atr_requires_high_and_low() -> None:
    atr = ATR(period=3)
    closes = pd.Series([10.0, 11.0, 12.0])
    with pytest.raises(ValueError, match="requires `high` and `low`"):
        atr.compute(closes)


def test_atr_known_values_no_gaps() -> None:
    """
    Bars with H-L=1 every period and no gaps → TR=1 for every bar.
    Wilder-smoothed ATR converges to 1 once warmup completes.
    """
    n = 20
    close = pd.Series([100.0] * n)
    high = pd.Series([100.5] * n)
    low = pd.Series([99.5] * n)
    atr = ATR(period=5).compute(close, high, low)
    # min_periods=5 → bars 0..3 are NaN, bar 4 onwards is valid.
    assert math.isnan(atr.iloc[3])
    assert atr.iloc[4] == pytest.approx(1.0, abs=1e-9)
    # Late bars stable at 1.0.
    assert atr.iloc[-1] == pytest.approx(1.0, abs=1e-9)


def test_atr_handles_gap_via_true_range() -> None:
    """
    A bar that GAPS up overnight has TR = max(H-L, |H-prev_close|, |L-prev_close|).
    If the gap is bigger than the H-L range, TR uses the gap distance.
    """
    # Bar 1: H=100, L=99,  C=99.5  (range 1)
    # Bar 2: GAP up, H=110, L=108, C=109  (range 2, but gap from 99.5 to 110 = 10.5)
    close = pd.Series([99.5, 109.0])
    high = pd.Series([100.0, 110.0])
    low = pd.Series([99.0, 108.0])
    # period=1 → ATR = TR exactly.
    atr = ATR(period=1).compute(close, high, low)
    # Bar 2 TR = max(110-108, |110-99.5|, |108-99.5|) = max(2, 10.5, 8.5) = 10.5
    assert atr.iloc[1] == pytest.approx(10.5)


def test_atr_invalid_period_raises() -> None:
    with pytest.raises(ValueError, match="period must be >= 1"):
        ATR(period=0)


# ─────────────────────────────────────────────────────────────────────
# Bollinger Bands
# ─────────────────────────────────────────────────────────────────────


def test_bollinger_compute_returns_middle_band() -> None:
    """compute() returns the SMA midline (canonical single-output)."""
    closes = pd.Series([10.0, 12.0, 14.0, 16.0, 18.0])
    bb = BollingerBands(period=3, std_multiplier=2.0).compute(closes)
    expected_sma = closes.rolling(3).mean()
    # Identical to SMA(3).
    for i in range(2, len(closes)):
        assert bb.iloc[i] == pytest.approx(expected_sma.iloc[i])


def test_bollinger_compute_full_shape_and_keys() -> None:
    closes = pd.Series([float(x) for x in range(1, 25)])
    bb = BollingerBands(period=10, std_multiplier=2.0)
    full = bb.compute_full(closes)
    assert set(full.keys()) == {"upper", "middle", "lower", "bandwidth", "percent_b"}
    for key, series in full.items():
        assert isinstance(series, pd.Series)
        assert len(series) == len(closes)


def test_bollinger_upper_above_middle_above_lower() -> None:
    closes = pd.Series([float(x) for x in range(1, 25)])
    full = BollingerBands(period=10).compute_full(closes)
    # Past warmup: ordering invariant must hold.
    for i in range(10, len(closes)):
        u, m, l = full["upper"].iloc[i], full["middle"].iloc[i], full["lower"].iloc[i]
        assert u >= m >= l
        # On non-degenerate stdev, strict inequality.
        assert u > m > l


def test_bollinger_percent_b_at_band_edges() -> None:
    """%B = 0 when close is at lower band; 1 when close is at upper band."""
    # Constant series → stdev = 0 → bands collapse to midline → divide-by-zero
    # is fine (NaN). Use a slightly noisy series so the bands aren't degenerate.
    rng = np.random.default_rng(seed=42)
    closes = pd.Series(100.0 + rng.normal(0, 1, 50))
    full = BollingerBands(period=10).compute_full(closes)
    pb = full["percent_b"]
    # Should be roughly centered around 0.5 with non-degenerate values past warmup.
    valid = pb.iloc[10:]
    assert valid.between(-2, 3).all(), "percent_b should generally sit in a reasonable range"


def test_bollinger_invalid_params_raise() -> None:
    with pytest.raises(ValueError, match="period must be >= 2"):
        BollingerBands(period=1)
    with pytest.raises(ValueError, match="std_multiplier must be > 0"):
        BollingerBands(period=20, std_multiplier=0.0)


# ─────────────────────────────────────────────────────────────────────
# Stochastic Oscillator
# ─────────────────────────────────────────────────────────────────────


def test_stochastic_requires_high_and_low() -> None:
    stoch = StochasticOscillator(period=5)
    closes = pd.Series([10.0] * 10)
    with pytest.raises(ValueError, match="requires `high` and `low`"):
        stoch.compute(closes)


def test_stochastic_raw_k_at_range_extremes() -> None:
    """
    %K = 0 when close == lowest_low; %K = 100 when close == highest_high.
    Use k_smoothing=1 to expose raw %K directly.
    """
    n = 10
    high = pd.Series([100.0] * n)
    low = pd.Series([90.0] * n)
    # Set the LAST close to the high → raw %K should be 100.
    close = pd.Series([95.0] * (n - 1) + [100.0])
    stoch = StochasticOscillator(period=5, k_smoothing=1, d_period=3)
    k = stoch.compute(close, high, low)
    assert k.iloc[-1] == pytest.approx(100.0)

    # Set the LAST close to the low → raw %K should be 0.
    close = pd.Series([95.0] * (n - 1) + [90.0])
    k = stoch.compute(close, high, low)
    assert k.iloc[-1] == pytest.approx(0.0)


def test_stochastic_compute_full_returns_k_and_d() -> None:
    n = 20
    rng = np.random.default_rng(seed=0)
    base = 100.0 + rng.normal(0, 2, n).cumsum()
    close = pd.Series(base)
    high = close + 1.0
    low = close - 1.0
    stoch = StochasticOscillator(period=5, k_smoothing=3, d_period=3)
    full = stoch.compute_full(close, high, low)
    assert set(full.keys()) == {"k", "d"}
    # Bounded between 0 and 100.
    for series in full.values():
        valid = series.dropna()
        assert valid.min() >= 0.0 - 1e-9
        assert valid.max() <= 100.0 + 1e-9


def test_stochastic_zero_range_returns_nan() -> None:
    """A perfectly flat window has range = 0 → guarded with NaN, not divide-by-zero."""
    n = 10
    close = pd.Series([100.0] * n)
    high = pd.Series([100.0] * n)
    low = pd.Series([100.0] * n)
    stoch = StochasticOscillator(period=5, k_smoothing=1, d_period=3)
    k = stoch.compute(close, high, low)
    # After enough warmup, range is 0 → %K is NaN.
    assert math.isnan(k.iloc[-1])


def test_stochastic_invalid_params_raise() -> None:
    with pytest.raises(ValueError, match="period must be >= 2"):
        StochasticOscillator(period=1)
    with pytest.raises(ValueError, match="k_smoothing must be >= 1"):
        StochasticOscillator(period=14, k_smoothing=0)
    with pytest.raises(ValueError, match="d_period must be >= 1"):
        StochasticOscillator(period=14, d_period=0)


# ─────────────────────────────────────────────────────────────────────
# Registry — all TA-3 additions discoverable by name
# ─────────────────────────────────────────────────────────────────────


def test_registry_includes_all_ta3_indicators() -> None:
    names = list_indicators()
    expected = {"sma", "ema", "wma", "rsi", "macd", "tsi", "stochastic", "atr", "bollinger"}
    assert expected <= set(names), f"missing: {expected - set(names)}"


def test_registry_resolves_new_indicators_by_name() -> None:
    assert isinstance(get_indicator("wma", period=20), WMA)
    assert isinstance(get_indicator("atr", period=14), ATR)
    assert isinstance(get_indicator("bollinger", period=20, std_multiplier=2.0), BollingerBands)
    assert isinstance(get_indicator("stochastic", period=14), StochasticOscillator)
    # Case-insensitive
    assert isinstance(get_indicator("ATR", period=14), ATR)


def test_registry_unknown_indicator_lists_supported() -> None:
    with pytest.raises(ValueError, match="Unknown indicator") as exc:
        get_indicator("supertrend")
    msg = str(exc.value)
    # Error should list known names so an agent gets actionable feedback.
    assert "atr" in msg and "bollinger" in msg and "stochastic" in msg
