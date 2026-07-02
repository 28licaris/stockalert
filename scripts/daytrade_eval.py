"""
DT-2 gate — full-history evaluation of the day-trading setups with the
pre-registered walls:

  DEV     = 2006-2018   (selection may look here)
  HOLDOUT = 2019-2026 excluding 2024   (revealed once per idea)
  SANDBOX = 2024        (mined for hypothesis generation — excluded)

Runs every setup over every scanned symbol-day, dumps one per-trade parquet
per version, and prints DEV vs HOLDOUT per setup/side/slip plus per-year
avg-R strips for regime texture.

  poetry run python scripts/daytrade_eval.py --version v1
  poetry run python scripts/daytrade_eval.py --version v2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

from daytrade_base_rates import _load_year  # noqa: E402
from daytrade_setups import run_symbol_day  # noqa: E402

DEV_YEARS = list(range(2006, 2019))
HOLDOUT_YEARS = [2019, 2020, 2021, 2022, 2023, 2025, 2026]
SANDBOX = {2024}


def _run_year(year: int, version: str) -> pd.DataFrame:
    scan, bars = _load_year(year)
    if scan.empty or bars.empty:
        print(f"  {year}: no data — skipped")
        return pd.DataFrame()
    gap_by = {(r.day, r.symbol): r.gap_pct for r in scan.itertuples(index=False)}
    rs = bars[(bars.et_min >= 570) & (bars.et_min < 960)]
    rows = []
    for (sym, day), g in rs.groupby(["symbol", "et_date"], sort=False):
        gap = gap_by.get((day, sym))
        if gap is None or len(g) < 60:
            continue
        o, h, l, c, v = (g[k].to_numpy(dtype=float)
                         for k in ("open", "high", "low", "close", "volume"))
        for slip in (1.0, 2.0):
            for r in run_symbol_day(o, h, l, c, v, gap, slip_mult=slip, version=version):
                rows.append({"year": year, "day": day, "symbol": sym,
                             "gap_pct": gap, "slip": slip, "setup": r.setup,
                             "side": r.side, "r": r.r_mult, "exit": r.exit_reason,
                             "hold_min": r.hold_minutes, "entry_min": r.i})
    df = pd.DataFrame(rows)
    n1 = len(df[df.slip == 1.0]) if not df.empty else 0
    print(f"  {year}: {n1:,} trades (1× slip)", flush=True)
    return df


def _report(df: pd.DataFrame, label: str) -> None:
    print(f"\n=== {label} ===")
    hdr = (f"{'setup':19}{'side':6}{'slip':5}{'n':>7}{'win%':>7}{'avgR':>8}"
           f"{'PF':>7}{'stop%':>7}{'eod%':>6}")
    print(hdr); print("-" * len(hdr))
    for (setup, side, slip), g in df.groupby(["setup", "side", "slip"]):
        gross_w = g.r[g.r > 0].sum(); gross_l = -g.r[g.r < 0].sum()
        pf = gross_w / gross_l if gross_l > 0 else float("inf")
        print(f"{setup:19}{side:6}{slip:<5.0f}{len(g):>7}{(g.r > 0).mean()*100:>7.1f}"
              f"{g.r.mean():>8.3f}{pf:>7.2f}{(g.exit == 'stop').mean()*100:>7.0f}"
              f"{(g.exit == 'eod').mean()*100:>6.0f}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", choices=("v1", "v2"), required=True)
    a = ap.parse_args(argv)
    years = sorted(set(DEV_YEARS) | set(HOLDOUT_YEARS))
    print(f"evaluating {a.version} over {len(years)} years "
          f"(sandbox {sorted(SANDBOX)} excluded)…", flush=True)
    frames = [f for y in years if not (f := _run_year(y, a.version)).empty]
    df = pd.concat(frames, ignore_index=True)
    out = Path(f"data/dt_trades_all_{a.version}.parquet")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"\n{len(df):,} trade rows → {out}")

    dev = df[df.year.isin(DEV_YEARS)]
    hold = df[df.year.isin(HOLDOUT_YEARS)]
    _report(dev, f"{a.version} DEV 2006-2018")
    _report(hold, f"{a.version} HOLDOUT 2019-2026 (ex 2024)")

    print("\n=== per-year avg R (1× slip) — regime strip ===")
    strip = (df[df.slip == 1.0]
             .pivot_table(index="setup", columns="year", values="r", aggfunc="mean"))
    print(strip.round(2).to_string())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
