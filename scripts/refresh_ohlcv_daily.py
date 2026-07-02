"""
Incremental nightly refresh for CH `ohlcv_daily` (the segment-clean research /
paper-trading universe).

Default source (post flat-files, 2026-07-01): **Polygon REST grouped-daily** —
one call per missing trading day returns the whole US market. Each day is
archived RAW (unadjusted, full market) to Iceberg `equities.polygon_daily_raw`
(lake = ground truth), then the universe slice is split-adjusted and appended
to CH. Splits landing inside the window trigger a full re-adjusted reload for
the affected symbols (historical rows must be re-divided).

  poetry run python scripts/refresh_ohlcv_daily.py                 # REST, catch up to yesterday ET
  poetry run python scripts/refresh_ohlcv_daily.py --end 2026-07-01
  poetry run python scripts/refresh_ohlcv_daily.py --source lake   # legacy flat-files lake path

Exit code 1 when days are missing but the source yielded ZERO rows across the
whole window — a stale/broken source must be visible, not silently absorbed.
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


def _weekdays(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)
            if (start + timedelta(days=i)).weekday() < 5]


def _frame_to_raw_arrow(df):
    """Canonical frame → the arrow shape apply_adjustment expects."""
    import pyarrow as pa
    n = len(df)
    return pa.table({
        "symbol": pa.array(df["symbol"].tolist()),
        "timestamp": pa.array(df["timestamp"].tolist(), type=pa.timestamp("us", tz="UTC")),
        "open": pa.array(df["open"].astype(float).tolist(), type=pa.float64()),
        "high": pa.array(df["high"].astype(float).tolist(), type=pa.float64()),
        "low": pa.array(df["low"].astype(float).tolist(), type=pa.float64()),
        "close": pa.array(df["close"].astype(float).tolist(), type=pa.float64()),
        "volume": pa.array(df["volume"].astype(float).tolist(), type=pa.float64()),
        "vwap": pa.nulls(n, pa.float64()),
        "trade_count": pa.nulls(n, pa.float64()),
    })


def _read_archived_day(sink, d: date):
    """Return the already-archived full-market frame for `d` from
    equities.polygon_daily_raw, or None if the day isn't archived yet."""
    from pyiceberg.expressions import EqualTo
    from app.services.ingest.polygon_grouped_daily import day_timestamp_utc
    arr = sink._table.scan(
        row_filter=EqualTo("timestamp", day_timestamp_utc(d).isoformat()),
        selected_fields=("symbol", "timestamp", "open", "high", "low",
                         "close", "volume"),
    ).to_arrow()
    return arr.to_pandas() if arr.num_rows else None


def _fetch_paced(d: date, pace_s: float):
    """Grouped-daily with pacing + one long backoff on a 429 storm (basic
    REST plans cap requests/minute)."""
    import time
    from urllib3.exceptions import MaxRetryError
    from app.services.ingest.polygon_grouped_daily import fetch_grouped_daily
    try:
        return fetch_grouped_daily(d)
    except MaxRetryError:
        print(f"    {d}: rate-limited (429) — backing off 65s and retrying once")
        time.sleep(65)
        return fetch_grouped_daily(d)
    finally:
        time.sleep(pace_s)


def _refresh_from_rest(symbols: list[str], days: list[date], pace_s: float = 13.0) -> int:
    """Grouped-daily per missing day: read the lake archive when the day is
    already there (free re-runs), else one paced REST call + archive.
    Returns rows staged for CH."""
    import asyncio as _asyncio
    import pandas as pd
    from app.services.equities.sink import EquitiesIcebergSink
    from app.services.ingest.polygon_grouped_daily import SOURCE_TAG

    sink = EquitiesIcebergSink.for_polygon_daily_raw()
    universe = set(symbols)
    staged: list[pd.DataFrame] = []
    holidays = 0
    for d in days:
        df = _read_archived_day(sink, d)
        if df is not None:
            print(f"    {d}: {len(df):,} rows from lake archive")
        else:
            df = _fetch_paced(d, pace_s)
            if df.empty:
                holidays += 1
                print(f"    {d}: no market data (holiday, or not yet published) — skipped")
                continue
            res = _asyncio.run(sink.write(df, file_date=d, kind="day", provider=SOURCE_TAG))
            if res.status == "error":
                raise RuntimeError(f"polygon_daily_raw append failed for {d}: {res.error}")
            print(f"    {d}: fetched {len(df):,} market rows · archived → polygon_daily_raw")
        in_uni = df[df["symbol"].isin(universe)]
        staged.append(in_uni)

    if not staged:
        if holidays == len(days):
            print("all missing days were holidays — nothing to do")
            return 0
        print("FAIL: grouped-daily returned ZERO rows across the window — "
              "check the Polygon REST key / subscription")
        return -1

    all_days = pd.concat(staged, ignore_index=True)
    lookup = _splits_lookup(symbols)
    adj = apply_adjustment(_frame_to_raw_arrow(all_days), lookup)
    cols = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]
    out = adj.select(cols).to_pandas()
    out["timestamp"] = out["timestamp"].dt.tz_localize(None)
    from app.db.client import get_client
    get_client().insert("ohlcv_daily", out[cols].values.tolist(), column_names=cols)
    print(f"  appended {len(out):,} adjusted daily rows → ohlcv_daily "
          f"({out['symbol'].nunique()} symbols, {len(staged)} day(s))")
    return len(out)


def _refresh_from_lake(symbols: list[str], start: date, end: date) -> int:
    """Legacy path: aggregate the flat-files minute lake (Athena)."""
    if not settings.polygon_nightly_enabled:
        print("SKIP: equities lake ingest disabled (POLYGON_NIGHTLY_ENABLED=false) — "
              "use --source rest (grouped-daily) instead")
        return 0
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
    if t.num_rows == 0:
        print(f"FAIL: lake returned ZERO rows for {start}..{end} — the lake is stale")
        return -1
    dates = np.array(t.column("d").to_pylist(), dtype="datetime64[D]").astype("datetime64[s]")
    ts = (dates + np.timedelta64(14 * 3600 + 30 * 60, "s")).astype("datetime64[us]")
    n = t.num_rows
    raw = pa.table({
        "symbol": t.column("symbol"),
        "timestamp": pa.array(ts, type=pa.timestamp("us", tz="UTC")),
        "open": t.column("open").cast(pa.float64()), "high": t.column("high").cast(pa.float64()),
        "low": t.column("low").cast(pa.float64()), "close": t.column("close").cast(pa.float64()),
        "volume": t.column("volume").cast(pa.float64()),
        "vwap": pa.nulls(n, pa.float64()), "trade_count": pa.nulls(n, pa.float64()),
    })
    lookup = _splits_lookup(symbols)
    adj = apply_adjustment(raw, lookup)
    cols = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]
    df = adj.select(cols).to_pandas()
    df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    from app.db.client import get_client
    get_client().insert("ohlcv_daily", df[cols].values.tolist(), column_names=cols)
    print(f"  appended {len(df):,} adjusted daily rows → ohlcv_daily")
    return len(df)


def _reload_split_symbols(symbols: list[str], start: date, end: date) -> None:
    """Splits with ex_date inside the window invalidate the symbol's HISTORY
    (prices divide by the cumulative future factor) → full re-adjusted reload."""
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


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--end", default=None, help="last ET date to fill (default: yesterday ET)")
    ap.add_argument("--source", choices=("rest", "lake"), default="rest",
                    help="rest = Polygon grouped-daily (default); lake = flat-files minute lake")
    a = ap.parse_args(argv)

    symbols = _universe()
    ch_max = _ch_max_date()
    if ch_max is None:
        print("ohlcv_daily is EMPTY — run the full builder (build_ohlcv_daily.py --universe --staging)")
        return 1
    end = date.fromisoformat(a.end) if a.end else _yesterday_et()
    start = ch_max + timedelta(days=1)
    if start > end:
        print(f"ohlcv_daily up to date (max={ch_max}, target end={end}) — nothing to do")
        return 0
    days = _weekdays(start, end)
    print(f"refreshing ohlcv_daily via {a.source}: {start} → {end} "
          f"({len(days)} weekday(s), {len(symbols)} symbols)…", flush=True)

    if a.source == "rest":
        n = _refresh_from_rest(symbols, days)
    else:
        n = _refresh_from_lake(symbols, start, end)
    if n < 0:
        return 1
    if n > 0:
        _reload_split_symbols(symbols, start, end)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
