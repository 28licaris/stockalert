"""
Materialize a CH `ohlcv_daily` research table from the lake — split-ADJUSTED daily
bars for the liquid, delisted-inclusive universe.

Pipeline (correctness-critical, so validated before trusting):
  1. Athena aggregates polygon_raw MINUTE → regular-session (09:30-16:00 ET) DAILY
     bars, bucketed by ET trading day (avoids the UTC-date misclassification bug).
  2. Reuse the tested `adjust.apply_adjustment` with market_corp_actions splits
     (prices ÷ cum-future-split-factor, volume ×) — same adjustment as the live path.
  3. Load into CH ohlcv_daily.

  --create                 create the table
  --symbols AAPL,NVDA      load + VALIDATE those vs BarReader's existing 1d rollup
  --universe               load all of configs/liquid_universe.txt
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


def create_table() -> None:
    from app.db.client import get_client  # targets the `stocks` DB (same as ohlcv_1m)
    get_client().command("""
        CREATE TABLE IF NOT EXISTS ohlcv_daily (
            symbol    LowCardinality(String),
            timestamp DateTime64(3, 'UTC'),
            open Float64, high Float64, low Float64, close Float64, volume Float64,
            version UInt64 DEFAULT toUnixTimestamp64Milli(now64(3))
        ) ENGINE = ReplacingMergeTree(version)
        PARTITION BY toYear(timestamp) ORDER BY (symbol, timestamp)
    """)
    print("ohlcv_daily table ready")


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


def load(symbols: list[str]) -> int:
    from app.db.client import get_client
    lookup = _splits_lookup(symbols)
    raw = _daily_raw(symbols)
    adj = apply_adjustment(raw, lookup)
    cols = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]
    py = adj.select(cols).to_pylist()
    # normalize timestamp to naive UTC datetime for clickhouse-connect
    rows = [[r["symbol"], r["timestamp"].replace(tzinfo=None), r["open"], r["high"],
             r["low"], r["close"], r["volume"]] for r in py]
    cli = get_client()
    CHUNK = 400_000
    for i in range(0, len(rows), CHUNK):
        cli.insert("ohlcv_daily", rows[i:i + CHUNK], column_names=cols)
    print(f"  inserted {len(rows):,} adjusted daily rows")
    return len(rows)


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
    a = ap.parse_args(argv)
    if a.create:
        create_table()
    syms = []
    if a.symbols:
        syms = [s.strip().upper() for s in a.symbols.split(",") if s.strip()]
    elif a.universe:
        syms = [s for s in Path("configs/liquid_universe.txt").read_text().split(",") if s]
    if syms:
        print(f"loading {len(syms)} symbols → ohlcv_daily (one Athena scan)…", flush=True)
        # One scan for the whole set: bucket(32,symbol) partitioning means batching
        # by symbol wouldn't prune much, so a single query is far cheaper than N.
        load(syms)
    for v in [x for x in [a.validate] + syms if x] if a.validate or a.symbols else []:
        validate(v)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
