"""
DT-1 — base-rate report: run every setup detector over the candidate store and
report honest per-setup outcomes at 1× and 2× slippage. This is the decision
checkpoint: families with no after-cost expectancy here die before any
portfolio machinery is built.

  poetry run python scripts/daytrade_base_rates.py --year 2024
  poetry run python scripts/daytrade_base_rates.py --year 2024 --dump data/dt_trades_2024.parquet
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from daytrade_setups import run_symbol_day  # noqa: E402


def _load_year(year: int):
    from app.db.client import get_client
    cli = get_client()
    scan = pd.DataFrame(
        cli.query(
            "SELECT day, symbol, gap_pct FROM daytrade_scan FINAL "
            "WHERE toYear(day) = {y:UInt16}", parameters={"y": year}).result_rows,
        columns=["day", "symbol", "gap_pct"])
    bars = pd.DataFrame(
        cli.query(
            "SELECT symbol, timestamp, open, high, low, close, volume "
            "FROM ohlcv_1m_candidates FINAL "
            "WHERE toYear(timestamp) = {y:UInt16} ORDER BY symbol, timestamp",
            parameters={"y": year}).result_rows,
        columns=["symbol", "timestamp", "open", "high", "low", "close", "volume"])
    ts = pd.to_datetime(bars["timestamp"]).dt.tz_localize("UTC").dt.tz_convert("America/New_York")
    bars["et_date"] = ts.dt.date
    bars["et_min"] = ts.dt.hour * 60 + ts.dt.minute
    return scan, bars


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--year", type=int, required=True)
    ap.add_argument("--dump", default="", help="write per-trade parquet")
    a = ap.parse_args(argv)

    scan, bars = _load_year(a.year)
    print(f"{a.year}: {len(scan):,} scan picks · {len(bars):,} candidate 1m bars")
    gap_by = {(r.day, r.symbol): r.gap_pct for r in scan.itertuples(index=False)}

    rs = bars[(bars.et_min >= 570) & (bars.et_min < 960)]
    rows, days_no_bars = [], 0
    for (sym, day), g in rs.groupby(["symbol", "et_date"], sort=False):
        gap = gap_by.get((day, sym))
        if gap is None:
            continue
        if len(g) < 60:
            days_no_bars += 1
            continue
        o, h, l, c, v = (g[k].to_numpy(dtype=float)
                         for k in ("open", "high", "low", "close", "volume"))
        for slip in (1.0, 2.0):
            for r in run_symbol_day(o, h, l, c, v, gap, slip_mult=slip):
                rows.append({
                    "day": day, "symbol": sym, "gap_pct": gap, "slip": slip,
                    "setup": r.setup, "side": r.side, "r": r.r_mult,
                    "exit": r.exit_reason, "hold_min": r.hold_minutes,
                    "entry_min": r.i,
                })
    if days_no_bars:
        print(f"  skipped {days_no_bars} symbol-days with <60 regular-session bars")
    df = pd.DataFrame(rows)
    if df.empty:
        print("NO TRIGGERS — check detectors / data")
        return 1
    if a.dump:
        Path(a.dump).parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(a.dump, index=False)
        print(f"  per-trade dump → {a.dump}")

    print(f"\n=== base rates {a.year} (one trigger per setup per symbol-day; "
          f"2R resting target; EOD 15:55 flat) ===")
    hdr = (f"{'setup':15}{'side':6}{'slip':5}{'n':>6}{'win%':>7}{'avgR':>8}"
           f"{'medR':>8}{'PF':>7}{'stop%':>7}{'tgt%':>6}{'eod%':>6}{'hold':>6}")
    print(hdr); print("-" * len(hdr))
    for (setup, side, slip), g in df.groupby(["setup", "side", "slip"]):
        wins = g.r > 0
        gross_w = g.r[g.r > 0].sum()
        gross_l = -g.r[g.r < 0].sum()
        pf = gross_w / gross_l if gross_l > 0 else float("inf")
        print(f"{setup:15}{side:6}{slip:<5.0f}{len(g):>6}{wins.mean()*100:>7.1f}"
              f"{g.r.mean():>8.3f}{g.r.median():>8.3f}{pf:>7.2f}"
              f"{(g.exit == 'stop').mean()*100:>7.0f}{(g.exit == 'target').mean()*100:>6.0f}"
              f"{(g.exit == 'eod').mean()*100:>6.0f}{g.hold_min.mean():>6.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
