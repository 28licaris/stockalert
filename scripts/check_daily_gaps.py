"""
Gap audit for CH ohlcv_daily. Uses SPY as the trading calendar; for each symbol,
'expected' = trading days between its first and last bar (so delisted names ending
early are NOT flagged as gaps). Reports missing-within-range gaps + universe symbols
that loaded zero rows.

  poetry run python scripts/check_daily_gaps.py [--fix]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tol", type=float, default=0.01, help="gap_pct threshold to flag")
    ap.add_argument("--finalize", type=int, default=0,
                    help="keep top-N gap-clean symbols (liquidity order), rewrite "
                         "configs/liquid_universe.txt, and prune the rest from CH")
    a = ap.parse_args(argv)
    from app.db.client import get_client
    c = get_client()

    cov = c.query(
        "SELECT symbol, min(toDate(timestamp)) lo, max(toDate(timestamp)) hi, count() n "
        "FROM ohlcv_daily GROUP BY symbol").result_rows
    spy = c.query("SELECT toDate(timestamp) d FROM ohlcv_daily WHERE symbol='SPY' ORDER BY d").result_rows
    cal = np.array([r[0] for r in spy])
    covmap = {r[0]: r for r in cov}
    print(f"ohlcv_daily: {len(cov)} symbols · SPY calendar {len(cal)} trading days "
          f"({cal.min()} → {cal.max()})\n")

    uni = [s for s in Path("configs/liquid_universe.txt").read_text().split(",") if s]
    present = {r[0] for r in cov}
    zero = sorted(set(uni) - present)

    gappy = []
    total_missing = 0
    for sym, lo, hi, n in cov:
        exp = int(np.searchsorted(cal, hi, "right") - np.searchsorted(cal, lo, "left"))
        miss = max(0, exp - n)
        total_missing += miss
        if exp and miss / exp > a.tol:
            gappy.append((sym, lo, hi, n, exp, miss, miss / exp))

    print(f"universe symbols: {len(uni)} · loaded: {len(present)} · ZERO rows: {len(zero)}")
    if zero:
        print(f"  zero-row (no lake data / renamed): {', '.join(zero[:40])}{' …' if len(zero)>40 else ''}")
    print(f"\ntotal missing-within-range bars: {total_missing:,}")
    print(f"symbols with >{a.tol*100:.0f}% internal gaps: {len(gappy)}")
    for sym, lo, hi, n, exp, miss, pct in sorted(gappy, key=lambda x: -x[6])[:25]:
        print(f"  {sym:8} {lo}→{hi}  have {n:5} / exp {exp:5}  miss {miss:4} ({pct*100:.1f}%)")
    healthy = len(present) - len(gappy)
    print(f"\n{healthy}/{len(present)} loaded symbols are gap-clean (<= {a.tol*100:.0f}% internal gaps)")

    if a.finalize:
        gappy_set = {g[0] for g in gappy}

        def _clean(sym: str) -> bool:
            return sym in present and sym not in gappy_set

        # walk the liquidity-ranked list, keep the first N gap-clean names
        clean = [s for s in uni if _clean(s)][: a.finalize]
        print(f"\n=== FINALIZE: selecting top {a.finalize} gap-clean by liquidity → kept {len(clean)} ===")
        Path("configs/liquid_universe.txt").write_text(",".join(clean))
        keep = set(clean)
        drop = [s for s in present if s not in keep]
        if drop:
            inlist = ",".join("'" + s.replace("'", "") + "'" for s in drop)
            c.command(f"ALTER TABLE ohlcv_daily DELETE WHERE symbol IN ({inlist})")
            print(f"  pruned {len(drop)} non-clean/surplus symbols from ohlcv_daily (mutation queued)")
        print(f"  clean universe written: {len(clean)} symbols → configs/liquid_universe.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
