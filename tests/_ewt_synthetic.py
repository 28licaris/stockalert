"""Synthetic OHLC builders for Elliott Wave tests.

A textbook 5-wave up-impulse + A-B-C correction, with a lead-in leg so the
impulse origin is a genuine detectable pivot (no left-window otherwise). The
down variant is the exact price reflection.
"""
from __future__ import annotations

import math

import pandas as pd

_LEGS_UP = [
    (108, 100),  # lead-in down — makes the 100 origin a real pivot low
    (100, 120),  # wave 1
    (120, 108),  # wave 2 (.60 retrace)
    (108, 150),  # wave 3 (extended)
    (150, 134),  # wave 4 (.38 retrace, no overlap)
    (134, 154),  # wave 5 (== wave 1)
    (154, 140),  # A
    (140, 148),  # B
    (148, 130),  # C
]
STEPS = 9


def _closes(legs) -> list[float]:
    out: list[float] = []
    for a, b in legs:
        for s in range(STEPS):
            base = a + (b - a) * (s / (STEPS - 1))
            wig = 0.6 * math.sin(s * 1.7) * (1 if (s % 2) else -1)
            out.append(round(base + wig, 2))
    return out


def synthetic_ohlc(direction: str = "up"):
    """Return (close, high, low) Series with a DatetimeIndex."""
    closes = _closes(_LEGS_UP)
    if direction == "down":
        closes = [round(200 - c, 2) for c in closes]
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    close = pd.Series(closes, index=idx)
    high = close + 0.2
    low = close - 0.2
    return close, high, low


# Mid-wave-3 as-of bar (with the lead-in, wave 3 spans bars 27..35).
AS_OF_WAVE3 = 32
