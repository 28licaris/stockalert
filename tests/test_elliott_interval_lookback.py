"""Per-interval lookback sanity tests.

_INTERVAL_LOOKBACK drives how many days of bars compute_labeling pulls.
Short intraday windows prevent thousands of irrelevant bars from flooding
pivot detection on 5m/15m charts.
"""
from __future__ import annotations

import pytest

from app.services.elliott_store.recompute import _INTERVAL_LOOKBACK, _DEFAULT_LOOKBACK


def test_daily_lookback_is_400():
    assert _INTERVAL_LOOKBACK["1d"] == 400


def test_intraday_lookbacks_are_shorter_than_daily():
    daily = _INTERVAL_LOOKBACK["1d"]
    for interval in ("1h", "30m", "15m", "5m", "1m"):
        assert _INTERVAL_LOOKBACK[interval] < daily, (
            f"{interval} lookback ({_INTERVAL_LOOKBACK[interval]}) should be < daily ({daily})"
        )


def test_lookbacks_decrease_with_timeframe():
    """Shorter bars → shorter lookback window."""
    order = ["1d", "4h", "1h", "30m", "15m", "5m", "1m"]
    present = [i for i in order if i in _INTERVAL_LOOKBACK]
    for a, b in zip(present, present[1:]):
        assert _INTERVAL_LOOKBACK[a] >= _INTERVAL_LOOKBACK[b], (
            f"{a} ({_INTERVAL_LOOKBACK[a]}) should be >= {b} ({_INTERVAL_LOOKBACK[b]})"
        )


def test_5m_lookback_is_at_most_two_weeks():
    """5m bars at ~78 bars/day: 10 days ≈ 780 bars — enough for EWT, not a flood."""
    assert _INTERVAL_LOOKBACK["5m"] <= 14


def test_default_lookback_covers_unknown_intervals():
    assert _DEFAULT_LOOKBACK == 400


@pytest.mark.parametrize("interval,expected", [
    ("1d", 400),
    ("1h",  90),
    ("15m", 20),
    ("5m",  10),
])
def test_known_interval_values(interval, expected):
    assert _INTERVAL_LOOKBACK[interval] == expected
