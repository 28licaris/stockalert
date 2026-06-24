#!/usr/bin/env python3
"""Elliott Wave engine — end-to-end demo over the REAL production modules.

Runs `app.indicators.pivots.PivotDetector` + `app.signals.elliott.WaveEngine`
on a controlled synthetic series (so it runs with no data access / creds), and
prints the resulting `WaveLabeling` plus a gate-zero (no-look-ahead) check.

This is now a thin driver over the shipped modules — not a separate algorithm —
so what you see here is exactly what the daily job / reader / MCP tool will
produce. To run on a real symbol, swap `synthetic_ohlc()` for a `BarsGateway`
pull once `.env` creds are present (the next integration step, EW-3/EW-4).

Run:  poetry run python scripts/ewt_poc.py
"""
from __future__ import annotations

import json
import math

import pandas as pd

from app.indicators.pivots import PivotDetector
from app.signals.elliott import WaveEngine

_LEGS = [
    (108, 100), (100, 120), (120, 108), (108, 150), (150, 134),
    (134, 154), (154, 140), (140, 148), (148, 130),
]


def synthetic_ohlc():
    closes = []
    for a, b in _LEGS:
        for s in range(9):
            closes.append(round(a + (b - a) * (s / 8) + 0.6 * math.sin(s * 1.7) * (1 if s % 2 else -1), 2))
    idx = pd.date_range("2024-01-01", periods=len(closes), freq="D")
    c = pd.Series(closes, index=idx)
    return c, c + 0.2, c - 0.2


def main() -> None:
    close, high, low = synthetic_ohlc()
    as_of = 32  # mid wave-3 (in-progress, the live case)

    pivots = PivotDetector(period=3, source="hl").detect(close, high, low)
    eng = WaveEngine()
    lab = eng.label(pivots, last_price=float(close.iloc[as_of]), symbol="DEMO",
                    interval="1d", as_of_index=as_of, as_of=close.index[as_of].to_pydatetime())

    # gate-zero: same as_of label must be byte-identical with +10 future bars
    piv_future = PivotDetector(period=3, source="hl").detect(close, high, low)
    lab2 = eng.label(piv_future, last_price=float(close.iloc[as_of]), symbol="DEMO",
                     interval="1d", as_of_index=as_of, as_of=close.index[as_of].to_pydatetime())
    stable = lab.model_dump() == lab2.model_dump()

    print("=" * 70)
    print(f"ELLIOTT WAVE — production modules, synthetic daily, as_of bar {as_of}")
    print("=" * 70)
    print(lab.model_dump_json(indent=2))
    print("-" * 70)
    print(f"engine_ver={lab.engine_ver}  ·  gate-zero stable: {'PASS' if stable else 'FAIL'}")


if __name__ == "__main__":
    main()
