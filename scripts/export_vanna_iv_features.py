"""
Export per-day vanna / IV aggregates from the ThetaData bronze (H-51).

For each (symbol, day): agg_vanna = sum(vanna x OI x 100) and
oiw_iv = OI-weighted mean implied vol, using the OI report-lag join
(report dated D = positions at close of the prior trading day — same
convention as the GEX derivation). Both features are consumed via
trailing-percentile ranks, so absolute scaling is irrelevant.

Merges columns into data/mcpt/gex_features.parquet (join on symbol+date).

  poetry run python scripts/export_vanna_iv_features.py --symbols SPY QQQ ...
"""
from __future__ import annotations

import argparse
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")

import pandas as pd  # noqa: E402

import app.config  # noqa: F401,E402
from app.config import settings  # noqa: E402
from app.services.iceberg_catalog import get_catalog  # noqa: E402
from app.services.options.tables import options_table_id  # noqa: E402

FEATURES = Path(__file__).resolve().parent.parent / "data" / "mcpt" / "gex_features.parquet"


def _scan(table, symbol: str, start: date, end: date, fields) -> pd.DataFrame:
    return table.scan(
        row_filter=(f"underlying_symbol == '{symbol}' and "
                    f"eod_date >= '{start.isoformat()}' and eod_date <= '{end.isoformat()}'"),
        selected_fields=fields,
    ).to_arrow().to_pandas()


def _symbol_features(catalog, symbol: str) -> pd.DataFrame:
    gt = catalog.load_table(options_table_id("thetadata_greeks_eod"))
    ot = catalog.load_table(options_table_id("thetadata_oi_eod"))
    out = []
    for p in pd.period_range("2016-01", str(pd.Period(date.today(), freq="M")), freq="M"):
        mstart, mend = p.start_time.date(), p.end_time.date()
        g = _scan(gt, symbol, mstart, mend,
                  ("eod_date", "expiration_date", "strike", "put_call", "vanna", "implied_vol"))
        if g.empty:
            continue
        oi_end = mend + pd.Timedelta(days=7).to_pytimedelta()
        oi = _scan(ot, symbol, mstart, oi_end,
                   ("eod_date", "expiration_date", "strike", "put_call", "open_interest"))
        if oi.empty:
            continue
        reports = sorted(oi["eod_date"].unique())
        prior = {reports[i]: reports[i - 1] for i in range(1, len(reports))}
        oi = oi[oi["eod_date"].isin(prior)]
        oi = oi.assign(position_date=oi["eod_date"].map(prior))
        m = g.merge(
            oi[["position_date", "expiration_date", "strike", "put_call", "open_interest"]]
              .rename(columns={"position_date": "eod_date"}),
            on=["eod_date", "expiration_date", "strike", "put_call"], how="inner",
        ).dropna(subset=["vanna", "implied_vol"])
        if m.empty:
            continue
        m["w"] = m["open_interest"].clip(lower=0)
        day = m.groupby("eod_date").apply(
            lambda x: pd.Series({
                "agg_vanna": float((x.vanna * x.w * 100).sum()),
                "oiw_iv": float((x.implied_vol * x.w).sum() / x.w.sum()) if x.w.sum() else None,
            }), include_groups=False)
        out.append(day)
    if not out:
        print(f"{symbol}: no data", flush=True)
        return pd.DataFrame()
    df = pd.concat(out).reset_index().rename(columns={"eod_date": "date"})
    df.insert(0, "symbol", symbol)
    print(f"{symbol}: {len(df)} days", flush=True)
    return df


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="*", default=None)
    a = ap.parse_args()
    symbols = a.symbols or sorted(
        s.strip().upper() for s in settings.options_snapshot_symbols.split(",") if s.strip())

    catalog = get_catalog()
    with ThreadPoolExecutor(max_workers=4) as pool:
        frames = [f for f in pool.map(lambda s: _symbol_features(catalog, s), symbols)
                  if not f.empty]
    if not frames:
        raise SystemExit("no vanna/iv features computed")
    new = pd.concat(frames, ignore_index=True)

    feats = pd.read_parquet(FEATURES)
    feats = feats.drop(columns=[c for c in ("agg_vanna", "oiw_iv") if c in feats.columns])
    merged = feats.merge(new, on=["symbol", "date"], how="outer")
    merged.sort_values(["symbol", "date"]).to_parquet(FEATURES, index=False)
    print(f"merged into {FEATURES}: {len(merged)} rows, "
          f"{merged['agg_vanna'].notna().sum()} with vanna/iv")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
