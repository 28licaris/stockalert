#!/usr/bin/env python3
"""Operator entrypoint for EW-3: ensure the elliott_wave_labels table, then
recompute + append labels for the given symbols.

Creating the Iceberg/Glue table is a deliberate op — it happens here on first
run, not on import. Append-only; re-running a day adds fresh rows.

Usage:
    poetry run python scripts/ewt_recompute.py AAPL NVDA TSLA
    poetry run python scripts/ewt_recompute.py --intervals 1d,1h AAPL
    poetry run python scripts/ewt_recompute.py /ES            # futures namespace
"""
from __future__ import annotations

import sys

from app.services.elliott_store import ensure_elliott_wave_labels, recompute_universe
from app.services.elliott_store.schema import asset_class_for


def main() -> None:
    args = sys.argv[1:]
    intervals: tuple[str, ...] = ("1d",)
    symbols: list[str] = []
    i = 0
    while i < len(args):
        if args[i] == "--intervals":
            intervals = tuple(x.strip() for x in args[i + 1].split(","))
            i += 2
        else:
            symbols.append(args[i])
            i += 1

    if not symbols:
        print("usage: ewt_recompute.py [--intervals 1d,1h] SYMBOL [SYMBOL ...]")
        return

    for ac in sorted({asset_class_for(s) for s in symbols}):
        ensure_elliott_wave_labels(ac)
        print(f"ensured elliott_wave_labels [{ac}]")

    summary = recompute_universe(symbols, intervals)
    print("recompute summary:", summary)


if __name__ == "__main__":
    main()
