"""
Incremental nightly refresh for CH `ohlcv_daily` (the segment-clean research /
paper-trading universe): aggregate ONLY the missing days from the lake and
append them. Splits landing inside the window trigger a full re-adjusted
reload for the affected symbols (historical rows must be re-divided).

  poetry run python scripts/refresh_ohlcv_daily.py            # catch up to yesterday (ET)
  poetry run python scripts/refresh_ohlcv_daily.py --end 2026-07-01

Exit code 1 when days are missing but the lake yielded ZERO rows — that means
the lake itself is stale (e.g. Polygon flat-files credentials broken) and the
failure must be visible, not silently absorbed.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_ohlcv_daily import _athena, _splits_lookup, load  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.equities.adjust import apply_adjustment  # noqa: E402

DB = settings.iceberg_equities_glue_database


def _universe() -> list[str]:
    return [s for s in Path("configs/liquid_universe.txt").read_text().split(",") if s]


def _ch_max_date():
    from app.db.client import get_client
    rows = get_client().query("SELECT max(toDate(timestamp)) FROM ohlcv_daily").result_rows
    return rows[0][0] if rows and rows[0][0] else None


def _yesterday_et() -> date:
    from app.services.equities.gaps import yesterday_et
    return yesterday_et()


def _daily_raw_window(symbols: list[str], start: date, end: date):
    """Same regular-session ET-bucketed aggregate as the full builder, but
    date-bounded so Athena prunes to the affected month partitions."""
    import numpy as np
    import pyarrow as pa
    inlist = ",".join(f"'{s}'" for s in symbols)
    t = _athena(
        f'SELECT "symbol", '
        f"date(\"timestamp\" AT TIME ZONE 'America/New_York') AS d, "
        f'min_by("open","timestamp") AS open, max("high") AS high, min("low") AS low, '
        f'max_by("close","timestamp") AS close, sum("volume") AS volume '
        f'FROM "{DB}"."polygon_raw" '
        f'WHERE "symbol" IN ({inlist}) AND "close" > 0 '
        f"AND \"timestamp\" >= from_iso8601_timestamp('{start.isoformat()}T00:00:00Z') "
        f"AND date(\"timestamp\" AT TIME ZONE 'America/New_York') "
        f"BETWEEN date '{start.isoformat()}' AND date '{end.isoformat()}' "
        f"AND (hour(\"timestamp\" AT TIME ZONE 'America/New_York')*60 "
        f"+ minute(\"timestamp\" AT TIME ZONE 'America/New_York')) BETWEEN 570 AND 959 "
        f'GROUP BY "symbol", date("timestamp" AT TIME ZONE \'America/New_York\')')
    dates = np.array(t.column("d").to_pylist(), dtype="datetime64[D]").astype("datetime64[s]")
    ts = (dates + np.timedelta64(14 * 3600 + 30 * 60, "s")).astype("datetime64[us]")
    n = t.num_rows
    return pa.table({
        "symbol": t.column("symbol"),
        "timestamp": pa.array(ts, type=pa.timestamp("us", tz="UTC")),
        "open": t.column("open").cast(pa.float64()), "high": t.column("high").cast(pa.float64()),
        "low": t.column("low").cast(pa.float64()), "close": t.column("close").cast(pa.float64()),
        "volume": t.column("volume").cast(pa.float64()),
        "vwap": pa.nulls(n, pa.float64()), "trade_count": pa.nulls(n, pa.float64()),
    })


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", default=None, help="last ET date to fill (default: yesterday ET)")
    a = ap.parse_args(argv)

    symbols = _universe()
    ch_max = _ch_max_date()
    if ch_max is None:
        print("ohlcv_daily is EMPTY — run the full builder (build_ohlcv_daily.py --universe --staging)")
        return 1
    if not settings.polygon_nightly_enabled:
        # Operator turned equities lake ingest OFF (e.g. flat-files subscription
        # lapsed) — a frozen table is the CONFIGURED state, not a failure.
        print(f"SKIP: equities lake ingest disabled (POLYGON_NIGHTLY_ENABLED=false) — "
              f"ohlcv_daily stays frozen at {ch_max}")
        return 0
    end = date.fromisoformat(a.end) if a.end else _yesterday_et()
    start = ch_max + timedelta(days=1)
    if start > end:
        print(f"ohlcv_daily up to date (max={ch_max}, target end={end}) — nothing to do")
        return 0
    n_days = sum(1 for i in range((end - start).days + 1)
                 if (start + timedelta(days=i)).weekday() < 5)
    print(f"refreshing ohlcv_daily: {start} → {end} ({n_days} weekday(s), "
          f"{len(symbols)} symbols)…", flush=True)

    raw = _daily_raw_window(symbols, start, end)
    if raw.num_rows == 0:
        print(f"FAIL: lake returned ZERO rows for {start}..{end} — the lake itself is "
              f"stale (check nightly_equities_polygon_refresh / flat-files credentials)")
        return 1

    lookup = _splits_lookup(symbols)
    adj = apply_adjustment(raw, lookup)
    cols = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]
    df = adj.select(cols).to_pandas()
    df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    from app.db.client import get_client
    cli = get_client()
    cli.insert("ohlcv_daily", df[cols].values.tolist(), column_names=cols)
    print(f"  appended {len(df):,} adjusted daily rows "
          f"({df['symbol'].nunique()} symbols, {df['timestamp'].dt.date.nunique()} day(s))")

    # Splits with ex_date inside the window invalidate the symbol's HISTORY
    # (prices divide by the cumulative future factor) → full re-adjusted
    # reload for those symbols. ReplacingMergeTree dedupes by version.
    t = _athena(
        f'SELECT DISTINCT "symbol" FROM "{DB}"."market_corp_actions" '
        f"WHERE \"action_type\"='split' AND \"factor\" IS NOT NULL AND \"factor\" != 1.0 "
        f"AND \"ex_date\" BETWEEN date '{start.isoformat()}' AND date '{end.isoformat()}'",
        timeout_s=120)
    split_syms = sorted({r["symbol"] for r in t.to_pylist()} & set(symbols))
    if split_syms:
        print(f"  {len(split_syms)} symbol(s) split in-window → full re-adjusted reload: "
              f"{', '.join(split_syms)}")
        load(split_syms, table="ohlcv_daily", trim=True)
    else:
        print("  no in-window splits — no historical re-adjustment needed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
