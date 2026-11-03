#!/usr/bin/env python3
"""
Build `vol_premium_history` — the premium series the percentile needs.

`options_atm_iv_daily` holds ten years of what options **cost**. This pairs
each of those days with a volatility forecast for the **same horizon**,
computed the way the live screen computes today's, giving a ratio per
symbol-day:

    vrp_ratio = implied_vol / forecast_vol

Percentiling that ratio is what turns "SNOW is at 1.37x" — unactionable,
because a positive premium is normal — into "SNOW is at its 96th percentile of
ten years", which is a reason to look today.

Percentiling implied vol ALONE would answer a different and much weaker
question ("is volatility high?"). The ratio is the point.

Measured over the full build: ratio p05 0.66, median 0.97, p95 1.39. The median
sitting slightly BELOW 1 is not a contradiction of the variance risk premium —
that is usually quoted against subsequent *realized* vol, whereas this is
against a *forecast*, and a GARCH long-run level fit through 2018/2020/2022
runs a little high in calm regimes. It also does not matter for the product: a
constant bias in the denominator cancels out of a per-symbol percentile, which
is precisely why the percentile is the headline and the bare ratio is not.

Forecasts come from `vol_premium.history`: GJR-GARCH parameters refit monthly
with the conditional variance rolled daily off realized returns, never using
the day being forecast. ~5,080 fits rather than ~87,000.

    poetry run python scripts/build_vol_premium_history.py --dry-run --symbols SPY
    poetry run python scripts/build_vol_premium_history.py
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.vol_premium.history import rolling_forecasts  # noqa: E402

TABLE = "vol_premium_history"
COLS = ["symbol", "eod_date", "days_to_expiration", "trading_days",
        "implied_vol", "forecast_vol", "vrp_ratio", "vrp_points",
        "sigma_next", "variance_ratio", "persistence", "fit_date",
        "source", "ingestion_ts", "version"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="", help="comma list; default = all in options_atm_iv_daily")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)

    from app.db.client import get_client
    from app.db.init import init_schema

    if not a.dry_run:
        init_schema()
    client = get_client()

    syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    if not syms:
        syms = [r[0] for r in client.query(
            f"SELECT DISTINCT symbol FROM options_atm_iv_daily FINAL ORDER BY symbol"
        ).result_rows]

    version = int(datetime.now(timezone.utc).timestamp() * 1000)
    run_id = f"volprem-hist-{datetime.now(timezone.utc):%Y%m%dT%H%M%S}"
    print(f"Vol premium history · {len(syms)} symbols{' · DRY RUN' if a.dry_run else ''}\n")

    total = 0
    thin: list[str] = []
    for i, sym in enumerate(syms, 1):
        iv_rows = client.query(
            "SELECT eod_date, days_to_expiration, atm_iv FROM options_atm_iv_daily FINAL "
            "WHERE symbol = {s:String} ORDER BY eod_date",
            parameters={"s": sym},
        ).result_rows
        bars = client.query(
            "SELECT toDate(timestamp), close FROM ohlcv_daily FINAL "
            "WHERE symbol = {s:String} ORDER BY timestamp",
            parameters={"s": sym},
        ).result_rows

        if not iv_rows or len(bars) < 700:
            thin.append(f"{sym}(iv={len(iv_rows)},bars={len(bars)})")
            continue

        dates = [b[0] for b in bars]
        closes = [float(b[1]) for b in bars]
        targets = {r[0]: int(r[1]) for r in iv_rows}
        iv_by_date = {r[0]: float(r[2]) for r in iv_rows}

        pts, skips = rolling_forecasts(dates, closes, targets)

        rows = []
        for p in pts:
            iv = iv_by_date.get(p.eod_date)
            if iv is None or p.forecast_vol <= 0:
                continue
            rows.append([sym, p.eod_date, targets[p.eod_date],
                         p.trading_days, iv, round(p.forecast_vol, 4),
                         round(iv / p.forecast_vol, 5), round(iv - p.forecast_vol, 4),
                         p.sigma_next, p.variance_ratio, p.persistence, p.fit_date,
                         "thetadata+garch", datetime.now(timezone.utc), version])

        total += len(rows)
        print(f"  [{i:>3}/{len(syms)}] {sym:<6} {len(rows):>5,} rows "
              f"(iv {len(iv_rows):,} · bars {len(bars):,}"
              + (f" · skips {skips}" if skips else "") + ")", flush=True)

        if rows and not a.dry_run:
            client.insert(TABLE, rows, column_names=COLS)

    print(f"\n  {total:,} rows")
    if thin:
        # Named, not silently absent: a symbol missing from the history has no
        # percentile, and the screen must be able to say so.
        print(f"  skipped {len(thin)} symbol(s) for thin inputs: {', '.join(thin[:12])}")

    if a.dry_run:
        return 0
    if total == 0:
        print("  FAILED — nothing written")
        return 1

    n, s, lo, hi = client.query(
        f"SELECT count(), uniqExact(symbol), min(eod_date), max(eod_date) FROM {TABLE} FINAL"
    ).result_rows[0]
    print(f"  {TABLE}: {n:,} rows · {s} symbols · {lo} → {hi}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
