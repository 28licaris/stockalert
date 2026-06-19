#!/usr/bin/env python3
"""Label a real symbol with the Elliott Wave engine — end-to-end on live lake data.

Pulls adjusted bars via the BarsGateway (lake source), runs the production
PivotDetector + WaveEngine, and prints the WaveLabeling for the latest bar.

Usage:
    poetry run python scripts/ewt_label.py [SYMBOL] [INTERVAL] [LOOKBACK_DAYS] [K]
    poetry run python scripts/ewt_label.py AAPL 1d 400 5
    poetry run python scripts/ewt_label.py /ES 1d 400 5      # futures (/-prefix)

Needs `.env` (AWS_PROFILE=stock-lake + provider creds) — copy it into the
worktree if missing.
"""
from __future__ import annotations

import json
import sys

import pandas as pd

from app.indicators.pivots import detect_multidegree
from app.services.readers.bars_gateway import BarSource, get_chart_bars
from app.signals.elliott import WaveEngine


def load_bars(symbol: str, interval: str, lookback_days: int):
    bars = get_chart_bars(symbol, interval=interval, lookback_days=lookback_days,
                          source=BarSource.LAKE)
    if not bars:
        return None
    df = pd.DataFrame([{"timestamp": b.timestamp, "open": b.open, "high": b.high,
                        "low": b.low, "close": b.close} for b in bars])
    df = df.sort_values("timestamp").set_index("timestamp")
    return df


def main() -> None:
    symbol = sys.argv[1] if len(sys.argv) > 1 else "AAPL"
    interval = sys.argv[2] if len(sys.argv) > 2 else "1d"
    lookback = int(sys.argv[3]) if len(sys.argv) > 3 else 400
    k = int(sys.argv[4]) if len(sys.argv) > 4 else 5

    df = load_bars(symbol, interval, lookback)
    if df is None or len(df) < 2 * k + 5:
        print(f"No usable bars for {symbol} ({interval}, {lookback}d). "
              f"Check creds / lake coverage.")
        return

    close, high, low = df["close"], df["high"], df["low"]
    # Multi-degree: let the engine synthesise across fractal degrees. `k` (arg 4)
    # sets the smallest degree; the ladder scales up from there.
    ks = tuple(x for x in (k, k * 2, k * 4, k * 8) if 2 * x + 5 <= len(close))
    pivots = detect_multidegree(close, high, low, ks=ks)
    as_of = len(close) - 1
    lab = WaveEngine().label(
        pivots, last_price=float(close.iloc[as_of]), symbol=symbol, interval=interval,
        as_of_index=as_of, as_of=close.index[as_of].to_pydatetime(),
    )

    print("=" * 72)
    print(f"{symbol}  {interval}  ·  {len(close)} bars  ·  "
          f"{close.index[0].date()} → {close.index[-1].date()}  ·  "
          f"last {float(close.iloc[-1]):.2f}  ·  degrees={ks}  ·  pivots={len(pivots)}")
    print("=" * 72)

    def _line(tag: str, c) -> str:
        if c is None:
            return f"{tag}: —"
        tgt = next(iter(c.fib_targets.values()), None)
        return (f"{tag}: {c.structure} {c.direction}, wave {c.current_wave}  "
                f"P={c.probability}  conf={c.confidence}  stop={c.invalidation_price}"
                + (f"  target={tgt}" if tgt else "")
                + f"\n      {c.rationale}")

    print(_line("PRIMARY  ", lab.primary))
    print(_line("SECONDARY", lab.secondary))
    print(f"uncertainty={lab.uncertainty}  ·  engine_ver={lab.engine_ver}")
    print("-" * 72)
    print(lab.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
