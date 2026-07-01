"""
Materialize a CH `ohlcv_daily` research table from the lake — split-ADJUSTED daily
bars for the liquid, delisted-inclusive universe.

Pipeline (correctness-critical, so validated before trusting):
  1. Athena aggregates polygon_raw MINUTE → regular-session (09:30-16:00 ET) DAILY
     bars, bucketed by ET trading day (avoids the UTC-date misclassification bug).
  2. Reuse the tested `adjust.apply_adjustment` with market_corp_actions splits
     (prices ÷ cum-future-split-factor, volume ×) — same adjustment as the live path.
  3. Segment-trim ticker-reuse contamination: Polygon keys rows by TICKER, so a
     reused ticker holds several companies' histories separated by multi-month
     gaps (V = Vivendi'06 → Visa'08+, COIN = Converted-Organics-era'07-10 →
     Coinbase'21+, FB = Facebook'12-22 + junk tail'25+). Keep only the dominant
     (max total dollar-volume) contiguous segment per symbol — otherwise the
     gap audit either rejects the whole name or a backtest trades the fake
     gap-jump splice between two unrelated companies.
  4. Load into CH ohlcv_daily (optionally via staging table + atomic EXCHANGE).

  --create                 create the table
  --symbols AAPL,NVDA      load + VALIDATE those vs BarReader's existing 1d rollup
  --universe               load all of configs/liquid_universe.txt
  --staging                load into ohlcv_daily_staging, then atomically EXCHANGE
  --no-segment-trim        keep full raw histories (debug/audit only)
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3  # noqa: E402
import pyarrow as pa  # noqa: E402
import pyarrow.csv as pacsv  # noqa: E402

from app.config import settings  # noqa: E402
from app.services.equities.adjust import apply_adjustment, build_cum_factor_lookup  # noqa: E402

DB = settings.iceberg_equities_glue_database
REGION = settings.stock_lake_region
OUT = f"s3://{settings.stock_lake_bucket}/athena-results/"


def _athena(sql: str, timeout_s: float = 600.0) -> pa.Table:
    cli = boto3.client("athena", region_name=REGION)
    qid = cli.start_query_execution(
        QueryString=sql, QueryExecutionContext={"Database": DB, "Catalog": "AwsDataCatalog"},
        ResultConfiguration={"OutputLocation": OUT}, WorkGroup="primary")["QueryExecutionId"]
    t0 = time.monotonic()
    while True:
        qe = cli.get_query_execution(QueryExecutionId=qid)["QueryExecution"]
        st = qe["Status"]["State"]
        if st in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            break
        if time.monotonic() - t0 > timeout_s:
            raise TimeoutError("athena timed out")
        time.sleep(2.0)
    if st != "SUCCEEDED":
        raise RuntimeError(f"athena {st}: {qe['Status'].get('StateChangeReason')}")
    loc = qe["ResultConfiguration"]["OutputLocation"]  # s3://bucket/key.csv
    _, _, rest = loc.partition("s3://")
    bucket, _, key = rest.partition("/")
    body = boto3.client("s3", region_name=REGION).get_object(Bucket=bucket, Key=key)["Body"].read()
    gb = qe.get("Statistics", {}).get("DataScannedInBytes", 0) / 1e9
    print(f"  (scanned {gb:.1f} GB)", flush=True)
    return pacsv.read_csv(io.BytesIO(body))


def create_table(name: str = "ohlcv_daily") -> None:
    from app.db.client import get_client  # targets the `stocks` DB (same as ohlcv_1m)
    get_client().command(f"""
        CREATE TABLE IF NOT EXISTS {name} (
            symbol    LowCardinality(String),
            timestamp DateTime64(3, 'UTC'),
            open Float64, high Float64, low Float64, close Float64, volume Float64,
            version UInt64 DEFAULT toUnixTimestamp64Milli(now64(3))
        ) ENGINE = ReplacingMergeTree(version)
        PARTITION BY toYear(timestamp) ORDER BY (symbol, timestamp)
    """)
    print(f"{name} table ready")


def dominant_segment_bounds(cal_idx: "np.ndarray", dollars: "np.ndarray",
                            max_gap: int = 5) -> tuple[int, int]:
    """Pure segment picker. cal_idx: sorted positions of a symbol's bars on the
    trading calendar; dollars: per-bar dollar volume. A segment breaks where more
    than max_gap consecutive trading days are missing (halts are shorter;
    ticker-reuse gaps are months). Returns (start, end) INDICES into cal_idx
    (inclusive) of the segment with the highest total dollar volume."""
    import numpy as np
    if len(cal_idx) == 0:
        raise ValueError("empty symbol history")
    breaks = np.where(np.diff(cal_idx) - 1 > max_gap)[0]
    starts = np.concatenate(([0], breaks + 1))
    ends = np.concatenate((breaks, [len(cal_idx) - 1]))
    seg_dollars = [dollars[s:e + 1].sum() for s, e in zip(starts, ends)]
    best = int(np.argmax(seg_dollars))
    return int(starts[best]), int(ends[best])


def segment_trim(df: "pd.DataFrame", max_gap: int = 5) -> "pd.DataFrame":
    """Keep each symbol's dominant contiguous segment. df needs columns
    symbol/timestamp/close/volume. Calendar = business days over the batch span,
    unioned with observed bar dates (so gap size is measured against real market
    days, independent of batch density; holidays inflate a gap by at most ~2 days,
    negligible vs months-long ticker-reuse gaps). Logs every trimmed symbol."""
    import numpy as np
    import pandas as pd
    ts = pd.to_datetime(df["timestamp"])
    if ts.dt.tz is not None:
        ts = ts.dt.tz_localize(None)
    dates = ts.dt.normalize()
    observed = dates.unique()
    calendar = np.sort(pd.bdate_range(observed.min(), observed.max()).union(
        pd.DatetimeIndex(observed)).values)
    df = df.assign(_cal=np.searchsorted(calendar, dates.values),
                   _dollar=(df["close"] * df["volume"]).values)
    keep_masks, trimmed = [], []
    for sym, g in df.groupby("symbol", sort=False):
        g = g.sort_values("_cal")
        s, e = dominant_segment_bounds(g["_cal"].values, g["_dollar"].values, max_gap)
        idx = g.index[s:e + 1]
        keep_masks.append(idx)
        if len(idx) != len(g):
            lo, hi = dates.loc[idx].min().date(), dates.loc[idx].max().date()
            trimmed.append((sym, len(g) - len(idx), lo, hi))
    kept = df.loc[np.concatenate([m.values for m in keep_masks])].drop(columns=["_cal", "_dollar"])
    if trimmed:
        print(f"  segment-trim: {len(trimmed)} symbols trimmed "
              f"({len(df) - len(kept):,} contaminated rows dropped):")
        for sym, dropped, lo, hi in sorted(trimmed, key=lambda t: -t[1])[:25]:
            print(f"    {sym:8} kept {lo}→{hi}  (dropped {dropped} rows)")
        if len(trimmed) > 25:
            print(f"    … and {len(trimmed) - 25} more")
    else:
        print("  segment-trim: no symbols required trimming")
    return kept


def _splits_lookup(symbols: list[str]):
    inlist = ",".join(f"'{s}'" for s in symbols)
    t = _athena(
        f'SELECT "symbol","ex_date","factor" FROM "{DB}"."market_corp_actions" '
        f"WHERE \"action_type\"='split' AND \"factor\" IS NOT NULL AND \"factor\" != 1.0 "
        f'AND "symbol" IN ({inlist})', timeout_s=120)
    rows = [(r["symbol"], r["ex_date"], float(r["factor"])) for r in t.to_pylist()]
    print(f"  {len(rows)} split events for {len(symbols)} symbols")
    return build_cum_factor_lookup(rows)


def _daily_raw(symbols: list[str]) -> pa.Table:
    inlist = ",".join(f"'{s}'" for s in symbols)
    # regular session 09:30-16:00 ET; bucket by ET trading day
    t = _athena(
        f'SELECT "symbol", '
        f"date(\"timestamp\" AT TIME ZONE 'America/New_York') AS d, "
        f'min_by("open","timestamp") AS open, max("high") AS high, min("low") AS low, '
        f'max_by("close","timestamp") AS close, sum("volume") AS volume '
        f'FROM "{DB}"."polygon_raw" '
        f'WHERE "symbol" IN ({inlist}) AND "close" > 0 '
        f"AND (hour(\"timestamp\" AT TIME ZONE 'America/New_York')*60 "
        f"+ minute(\"timestamp\" AT TIME ZONE 'America/New_York')) BETWEEN 570 AND 959 "
        f'GROUP BY "symbol", date("timestamp" AT TIME ZONE \'America/New_York\')')
    # daily timestamp = ET date at 14:30 UTC (same UTC calendar date → adjustment matches)
    import numpy as np
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


def load(symbols: list[str], table: str = "ohlcv_daily", trim: bool = True) -> int:
    from app.db.client import get_client
    lookup = _splits_lookup(symbols)
    raw = _daily_raw(symbols)
    adj = apply_adjustment(raw, lookup)
    cols = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]
    df = adj.select(cols).to_pandas()
    if trim:
        df = segment_trim(df)
    df["timestamp"] = df["timestamp"].dt.tz_localize(None)  # naive UTC for clickhouse-connect
    rows = df[cols].values.tolist()
    cli = get_client()
    CHUNK = 400_000
    for i in range(0, len(rows), CHUNK):
        cli.insert(table, rows[i:i + CHUNK], column_names=cols)
    print(f"  inserted {len(rows):,} adjusted daily rows → {table}")
    return len(rows)


def exchange_staging() -> None:
    """Atomically swap ohlcv_daily_staging into place; old data lands in staging
    and is dropped. Zero-downtime for concurrent readers."""
    from app.db.client import get_client
    cli = get_client()
    n_new = cli.query("SELECT count() FROM ohlcv_daily_staging").result_rows[0][0]
    if not n_new:
        raise RuntimeError("staging table is empty — refusing to exchange")
    cli.command("EXCHANGE TABLES ohlcv_daily AND ohlcv_daily_staging")
    n_old = cli.query("SELECT count() FROM ohlcv_daily_staging").result_rows[0][0]
    cli.command("DROP TABLE ohlcv_daily_staging")
    print(f"  exchanged: ohlcv_daily now {n_new:,} rows (replaced {n_old:,}); staging dropped")


def validate(symbol: str) -> None:
    from app.services.readers.bar_reader import BarReader
    from app.db.client import get_client
    ref = {b.timestamp.date(): b.close for b in BarReader.from_settings().get_bars_in_range(
        symbol, datetime(2018, 1, 1, tzinfo=timezone.utc), datetime(2024, 12, 31, tzinfo=timezone.utc),
        interval="1d")}
    got = get_client().query(
        "SELECT toDate(timestamp) d, close FROM ohlcv_daily WHERE symbol={s:String} "
        "AND timestamp >= '2018-01-01' AND timestamp < '2025-01-01' ORDER BY d",
        parameters={"s": symbol}).result_rows
    if not ref or not got:
        print(f"  VALIDATE {symbol}: ref={len(ref)} rollup bars, ours={len(got)} — cannot compare"); return
    diffs, checked = [], 0
    for d, c in got:
        if d in ref and ref[d] > 0:
            checked += 1
            diffs.append(abs(c - ref[d]) / ref[d])
    if not checked:
        print(f"  VALIDATE {symbol}: no overlapping dates"); return
    import statistics
    med = statistics.median(diffs); mx = max(diffs)
    print(f"  VALIDATE {symbol}: {checked} overlapping days · median rel-diff {med*100:.3f}% · max {mx*100:.2f}%"
          f"  → {'OK ✓' if med < 0.01 else 'MISMATCH ✗ (check bucketing/adjustment)'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--symbols", default="")
    ap.add_argument("--universe", action="store_true")
    ap.add_argument("--validate", default="")
    ap.add_argument("--staging", action="store_true",
                    help="load into ohlcv_daily_staging then atomically EXCHANGE")
    ap.add_argument("--no-segment-trim", action="store_true")
    a = ap.parse_args(argv)
    if a.create:
        create_table()
    syms = []
    if a.symbols:
        syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    elif a.universe:
        syms = [s for s in Path("configs/liquid_universe.txt").read_text().split(",") if s]
    if syms:
        table = "ohlcv_daily_staging" if a.staging else "ohlcv_daily"
        if a.staging:
            from app.db.client import get_client
            get_client().command("DROP TABLE IF EXISTS ohlcv_daily_staging")
            create_table("ohlcv_daily_staging")
        print(f"loading {len(syms)} symbols → {table} (one Athena scan)…", flush=True)
        # One scan for the whole set: bucket(32,symbol) partitioning means batching
        # by symbol wouldn't prune much, so a single query is far cheaper than N.
        load(syms, table=table, trim=not a.no_segment_trim)
        if a.staging:
            exchange_staging()
    for v in [x for x in [a.validate] + syms if x] if a.validate or a.symbols else []:
        validate(v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
