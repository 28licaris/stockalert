"""Golden tests for ADX (Wilder trend-strength)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.indicators.adx import ADX


def _series(vals):
    return pd.Series(vals, dtype="float64")


def test_adx_requires_high_low():
    with pytest.raises(ValueError):
        ADX(14).compute(_series([1, 2, 3]))


def test_adx_warmup_is_nan_then_bounded():
    n = 100
    close = _series([100 + i for i in range(n)])
    high = close * 1.02
    low = close * 0.98
    adx = ADX(14).compute(close, high, low)
    assert adx.iloc[:14].isna().all()          # warmup NaN
    valid = adx.dropna()
    assert ((valid >= 0) & (valid <= 100)).all()  # ADX is a 0..100 reading


def test_adx_high_in_strong_trend_low_in_chop():
    n = 120
    # Strong, persistent uptrend → +DM dominates → ADX should be high.
    up_close = _series([100 + i for i in range(n)])
    up = ADX(14).compute(up_close, up_close * 1.02, up_close * 0.98).iloc[-1]
    # Choppy: alternating up/down, no net direction → ADX should be low.
    chop_close = _series([100 + (2 if i % 2 else 0) for i in range(n)])
    chop = ADX(14).compute(chop_close, chop_close * 1.02, chop_close * 0.98).iloc[-1]
    assert up > 30, f"strong trend ADX should be high, got {up:.1f}"
    assert chop < up, f"chop ADX {chop:.1f} should be below trend ADX {up:.1f}"
