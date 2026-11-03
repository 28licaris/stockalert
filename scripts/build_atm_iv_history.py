"""Reduce the ThetaData EOD option history to a daily at-the-money IV series.

    poetry run python scripts/build_atm_iv_history.py --dry-run --start 2026-01
    poetry run python scripts/build_atm_iv_history.py                # full 2016 →
    poetry run python scripts/build_atm_iv_history.py --start 2025-01 --symbols SPY

Turns `options.thetadata_greeks_eod` (hundreds of millions of contract rows)
into `options_atm_iv_daily` in ClickHouse (~100k rows): one at-the-money implied
volatility per symbol per session.

This is the **baseline** the Vol Premium screen needs. Without it the screen can
only say "SNOW is at 1.37x", which is not actionable because a positive premium
is normal. With it the screen says "SNOW is at its 96th percentile of ten
years", which is a reason to look today.

The estimator is deliberately the same one the live screen uses — expiry nearest
30 days, near-the-money calls *and* puts averaged, one-sided expiries refused.
A baseline built by a different rule would make the percentile meaningless.

Work is done month by month, which matches the table's `month(eod_date)`
partitioning so each pass prunes to its own partition. Every month logs an
outcome including zero, and the run exits non-zero if any month failed
(`docs/standards/coding.md` — no silent failures).
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import polars as pl  # noqa: E402
from pyiceberg.expressions import (  # noqa: E402
    And,
    GreaterThanOrEqual,
    In,
    LessThan,
)

from app.services.options.tables import THETA_GREEKS_EOD_TABLE_NAME  # noqa: E402
from app.services.vol_premium.baseline import (  # noqa: E402
    OUTPUT_COLUMNS,
    reduce_eod_chains,
)
from app.services.vol_premium.schemas import ScreenParams  # noqa: E402

TABLE = "options_atm_iv_daily"
FIELDS = (
    "underlying_symbol", "eod_date", "expiration_date",
    "strike", "put_call", "implied_vol", "underlying_price",
)


def _months(start: str, end: str) -> list[tuple[date, date]]:
    y, m = (int(x) for x in start.split("-"))
    ey, em = (int(x) for x in end.split("-"))
    out = []
    while (y, m) <= (ey, em):
        ny, nm = (y + 1, 1) if m == 12 else (y, m + 1)
        out.append((date(y, m, 1), date(ny, nm, 1)))
        y, m = ny, nm
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--start", default="2016-01", help="YYYY-MM inclusive")
    ap.add_argument("--end", default=datetime.now(timezone.utc).strftime("%Y-%m"))
    ap.add_argument("--symbols", default="", help="comma list; default = all")
    ap.add_argument("--target-dte", type=int, default=30)
    ap.add_argument("--dry-run", action="store_true", help="reduce but do not write")
    a = ap.parse_args(argv)

    symbols = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    params = ScreenParams(target_dte=a.target_dte)
    months = _months(a.start, a.end)

    from app.services.iceberg_catalog import get_catalog

    table = get_catalog().load_table(f"options.{THETA_GREEKS_EOD_TABLE_NAME}")

    client = None
    if not a.dry_run:
        from app.db.client import get_client
        from app.db.init import init_schema

        init_schema()             # idempotent; creates options_atm_iv_daily
        client = get_client()

    print(f"ATM IV history · {a.start} → {a.end} · {len(months)} month(s) · "
          f"target {a.target_dte}d{' · DRY RUN' if a.dry_run else ''}\n")

    version = int(datetime.now(timezone.utc).timestamp() * 1000)
    total_raw = total_rows = 0
    failed: list[str] = []
    empty: list[str] = []

    for lo, hi in months:
        label = f"{lo:%Y-%m}"
        pred = And(GreaterThanOrEqual("eod_date", lo.isoformat()),
                   LessThan("eod_date", hi.isoformat()))
        if symbols:
            pred = And(pred, In("underlying_symbol", symbols))

        try:
            arrow = table.scan(row_filter=pred, selected_fields=FIELDS).to_arrow()
        except Exception as exc:
            print(f"  {label}  SCAN FAILED — {exc}")
            failed.append(label)
            continue

        if arrow.num_rows == 0:
            print(f"  {label}  no source rows")
            empty.append(label)
            continue

        try:
            out = reduce_eod_chains(pl.from_arrow(arrow), params=params)
        except Exception as exc:
            print(f"  {label}  REDUCE FAILED — {exc}")
            failed.append(label)
            continue

        total_raw += arrow.num_rows
        total_rows += out.height
        print(f"  {label}  {arrow.num_rows:>9,} raw → {out.height:>4,} daily rows "
              f"· {out['symbol'].n_unique():2} symbols", flush=True)

        if a.dry_run or out.height == 0:
            continue

        rows = [
            [r["symbol"], r["eod_date"], r["expiration_date"],
             int(r["days_to_expiration"]), float(r["atm_iv"]), int(r["contracts"]),
             int(r["calls"]), int(r["puts"]), float(r["underlying_price"]),
             "thetadata-eod", datetime.now(timezone.utc), version]
            for r in out.iter_rows(named=True)
        ]
        client.insert(TABLE, rows, column_names=OUTPUT_COLUMNS + [
            "source", "ingestion_ts", "version"
        ])

    print(f"\n{total_raw:,} raw contract rows → {total_rows:,} daily ATM rows")
    if empty:
        print(f"months with no source data: {len(empty)}  ({', '.join(empty[:6])}…)")

    if not a.dry_run and client is not None:
        n, syms, lo_d, hi_d = client.query(
            f"SELECT count(), uniqExact(symbol), min(eod_date), max(eod_date) "
            f"FROM {TABLE} FINAL"
        ).result_rows[0]
        print(f"\n{TABLE}: {n:,} rows · {syms} symbols · {lo_d} → {hi_d}")
        # Cross-side check: a write that did not change the read is a failure.
        if n == 0:
            print("FAILED — the table is empty after the run")
            return 1

    if failed:
        print(f"\nFAILED month(s): {failed}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
