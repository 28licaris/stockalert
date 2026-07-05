"""
Historical GEX derivation: ThetaData bronze -> options.gamma_exposure_snapshots.

Joins options.thetadata_greeks_eod (gamma at close of day T) with
options.thetadata_oi_eod and feeds the EXISTING aggregation pipeline
(app.services.options.parser.aggregate_gamma_exposure), so historical
rows land in the same table / API / UI as the live Schwab-derived GEX.

OI DATING (the lookahead trap — see 2026-07 ThetaData Discord notes):
OPRA publishes open interest once daily ~06:30 ET reflecting positions
as of the PRIOR close. With --oi-report-lag 1 (default), the OI row
dated D is treated as positions at close(D-1), so GEX at close(T) joins
greeks(T) x OI rows dated the NEXT trading day after T. Run
`thetadata_backfill.py --probe` and verify against a known chain before
trusting lag=0. The methodology string records the convention.

  poetry run python scripts/thetadata_gex_backfill.py --symbols SPY --start 2016-01 --end 2016-03
  poetry run python scripts/thetadata_gex_backfill.py            # full derive
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")
from datetime import date, datetime, time as dtime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402
import pyarrow as pa  # noqa: E402

import app.config  # noqa: F401,E402
from app.config import settings  # noqa: E402
from app.services.options.parser import aggregate_gamma_exposure  # noqa: E402
from app.services.options.schemas import OptionContractSnapshot  # noqa: E402
from app.services.options.sink import _GAMMA_ARROW  # noqa: E402
from app.services.options.tables import (  # noqa: E402
    ensure_gamma_exposure,
    ensure_theta_greeks_eod,
    ensure_theta_oi_eod,
)

ET = ZoneInfo("America/New_York")
MARKER_PREFIX = "thetadata_gex_markers"
METHODOLOGY = "stockalert-thetadata-gex-v1"


def _scan_month(table, symbol: str, start: date, end: date) -> pd.DataFrame:
    return table.scan(
        row_filter=(
            f"underlying_symbol == '{symbol}' and "
            f"eod_date >= '{start.isoformat()}' and eod_date <= '{end.isoformat()}'"
        ),
    ).to_arrow().to_pandas()


def _snapshot_ts(d: date) -> datetime:
    """Close-of-day timestamp: 16:00 ET on the greeks date, as UTC."""
    return datetime.combine(d, dtime(16, 0), tzinfo=ET).astimezone(timezone.utc)


def _derive_symbol_month(
    greeks_tbl, oi_tbl, symbol: str, month: pd.Period, oi_lag: int, run_id: str,
) -> list:
    mstart, mend = month.start_time.date(), month.end_time.date()
    g = _scan_month(greeks_tbl, symbol, mstart, mend)
    if g.empty:
        return []
    # OI window extends past month end to cover next-trading-day reports
    oi_end = mend + pd.Timedelta(days=7 if oi_lag else 0).to_pytimedelta()
    oi = _scan_month(oi_tbl, symbol, mstart, oi_end)
    if oi.empty:
        return []

    if oi_lag:
        # OI report dated D = positions at close of the prior trading day.
        # Map each report to the greeks date it describes.
        report_dates = sorted(oi["eod_date"].unique())
        prior = {report_dates[i]: report_dates[i - 1] for i in range(1, len(report_dates))}
        oi = oi[oi["eod_date"].isin(prior)]
        oi = oi.assign(position_date=oi["eod_date"].map(prior))
    else:
        oi = oi.assign(position_date=oi["eod_date"])

    key = ["position_date", "expiration_date", "strike", "put_call"]
    oi_small = oi[key + ["open_interest"]].rename(columns={"position_date": "eod_date"})
    merged = g.merge(oi_small, on=["eod_date", "expiration_date", "strike", "put_call"],
                     how="inner")
    if merged.empty:
        return []

    rows = []
    for eod, day in merged.groupby("eod_date"):
        contracts = [
            OptionContractSnapshot(
                underlying_symbol=symbol,
                option_symbol=(f"{symbol}_{r.expiration_date:%y%m%d}"
                               f"{'C' if r.put_call == 'CALL' else 'P'}{r.strike:g}"),
                snapshot_ts=_snapshot_ts(eod),
                put_call=r.put_call,
                expiration_date=r.expiration_date,
                strike=float(r.strike),
                underlying_price=None if pd.isna(r.underlying_price) else float(r.underlying_price),
                gamma=None if pd.isna(r.gamma) else float(r.gamma),
                delta=None if pd.isna(r.delta) else float(r.delta),
                volatility=None if pd.isna(r.implied_vol) else float(r.implied_vol),
                volume=None if pd.isna(r.volume) else int(r.volume),
                open_interest=int(r.open_interest),
                source="thetadata-eod",
            )
            for r in day.itertuples()
        ]
        rows.extend(aggregate_gamma_exposure(
            contracts,
            source_snapshot_id=f"thetadata:{symbol}:{eod.isoformat()}",
            methodology=f"{METHODOLOGY}+oi_lag{oi_lag}",
            ingestion_run_id=run_id,
            # strike_expiry drill-down skipped: dominates rows/compute on
            # dense chains and no registered hypothesis consumes it.
            levels=frozenset({"total", "strike", "expiry"}),
        ))
    return rows


def _gamma_arrow(rows, run_id: str) -> pa.Table:
    now = datetime.now(timezone.utc)
    recs = []
    for r in rows:
        d = r.model_dump()
        d["ingestion_ts"] = now
        d["ingestion_run_id"] = run_id
        d["source"] = "thetadata-eod"
        recs.append({f.name: d.get(f.name) for f in _GAMMA_ARROW})
    return pa.Table.from_pylist(recs, schema=_GAMMA_ARROW)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="*", default=None)
    ap.add_argument("--start", default="2016-01")
    ap.add_argument("--end", default=None)
    ap.add_argument("--oi-report-lag", type=int, default=1, choices=(0, 1))
    a = ap.parse_args()

    symbols = a.symbols or sorted(
        s.strip().upper() for s in settings.options_snapshot_symbols.split(",") if s.strip())
    end = a.end or str(pd.Period(date.today(), freq="M"))
    months = list(pd.period_range(a.start, end, freq="M"))
    run_id = f"thetadata-gex-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"

    greeks_tbl, oi_tbl, gamma_tbl = (
        ensure_theta_greeks_eod(), ensure_theta_oi_eod(), ensure_gamma_exposure())

    import boto3
    s3, bucket = boto3.client("s3"), settings.stock_lake_bucket
    done: set[str] = set()
    pager = s3.get_paginator("list_objects_v2")
    for page in pager.paginate(Bucket=bucket, Prefix=MARKER_PREFIX + "/"):
        done.update(obj["Key"] for obj in page.get("Contents", []))

    units = [(sym, m) for sym in symbols for m in months
             if f"{MARKER_PREFIX}/{sym}/{m}.json" not in done]
    print(f"plan: {len(units)} symbol-months ({len(done)} already derived) run={run_id}",
          flush=True)

    failures, total_rows, t0 = [], 0, time.time()
    for i, (sym, m) in enumerate(units, 1):
        try:
            rows = _derive_symbol_month(greeks_tbl, oi_tbl, sym, m, a.oi_report_lag, run_id)
            if rows:
                gamma_tbl.append(_gamma_arrow(rows, run_id))
            total_rows += len(rows)
            s3.put_object(Bucket=bucket, Key=f"{MARKER_PREFIX}/{sym}/{m}.json",
                          Body=json.dumps({"rows": len(rows), "run_id": run_id}))
            print(f"[{i}/{len(units)}] {sym} {m}: {len(rows)} gex rows "
                  f"({time.time() - t0:.0f}s)", flush=True)
        except Exception as e:  # noqa: BLE001 — collected, reported, non-zero exit
            failures.append((sym, str(m), str(e)))
            print(f"[{i}/{len(units)}] {sym} {m}: FAILED {e}", flush=True)

    print(f"\nderived {total_rows} rows | failures: {len(failures)}", flush=True)
    for f in failures[:20]:
        print(f"  FAILED {f}", flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
