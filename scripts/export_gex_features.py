"""
Export daily GEX research features from options.gamma_exposure_snapshots.

Per (symbol, date): net_gex (total-level signed dollar gamma), put_wall
(deepest negative strike-level GEX), call_wall (largest positive), spot.
Consumed by the EXP-55 screen families in scripts/mcpt_insample.py via
data/mcpt/gex_features.parquet.

KNOWABILITY: a day-T row uses OI positions as of T's close, PUBLISHED
T+1 ~06:30 ET (methodology +oi_lag1). Screens must lag features one day
(signal day t uses features of t-1) — enforced in the family functions.

  poetry run python scripts/export_gex_features.py --symbols SPY
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

os.environ.setdefault("AWS_REQUEST_CHECKSUM_CALCULATION", "when_required")

import pandas as pd  # noqa: E402

import app.config  # noqa: F401,E402
from app.services.iceberg_catalog import get_catalog  # noqa: E402
from app.services.options.tables import options_table_id  # noqa: E402

OUT = Path(__file__).resolve().parent.parent / "data" / "mcpt" / "gex_features.parquet"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="+", required=True)
    ap.add_argument("--source", default="thetadata-eod")
    a = ap.parse_args()

    table = get_catalog().load_table(options_table_id("gamma_exposure_snapshots"))
    feats = []
    for sym in a.symbols:
        df = table.scan(
            row_filter=(f"underlying_symbol == '{sym}' and source == '{a.source}' and "
                        "aggregation_level in ('total', 'strike')"),
            selected_fields=("underlying_symbol", "snapshot_ts", "aggregation_level",
                             "strike", "gamma_exposure", "underlying_price"),
        ).to_arrow().to_pandas()
        if df.empty:
            print(f"{sym}: NO ROWS (source={a.source}) — derivation not run?", flush=True)
            continue
        df["date"] = pd.to_datetime(df["snapshot_ts"], utc=True).dt.tz_convert(
            "America/New_York").dt.date
        total = df[df["aggregation_level"] == "total"]
        strikes = df[df["aggregation_level"] == "strike"]
        by_day = total.set_index("date")[["gamma_exposure", "underlying_price"]]
        by_day.columns = ["net_gex", "spot"]
        walls = strikes.groupby("date").apply(
            lambda g: pd.Series({
                "put_wall": (g.loc[g["gamma_exposure"].idxmin(), "strike"]
                             if (g["gamma_exposure"] < 0).any() else None),
                "call_wall": (g.loc[g["gamma_exposure"].idxmax(), "strike"]
                              if (g["gamma_exposure"] > 0).any() else None),
            }), include_groups=False)
        out = by_day.join(walls).reset_index()
        out.insert(0, "symbol", sym)
        feats.append(out)
        print(f"{sym}: {len(out)} days  net_gex[{out['net_gex'].min():.3g}, "
              f"{out['net_gex'].max():.3g}]  span {out['date'].min()}..{out['date'].max()}",
              flush=True)

    if not feats:
        raise SystemExit("no features exported")
    all_f = pd.concat(feats, ignore_index=True).sort_values(["symbol", "date"])
    OUT.parent.mkdir(parents=True, exist_ok=True)
    all_f.to_parquet(OUT, index=False)
    print(f"wrote {OUT} ({len(all_f)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
