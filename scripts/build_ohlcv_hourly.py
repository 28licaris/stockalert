"""
Materialize CH `ohlcv_hourly` — split-ADJUSTED hourly bars for the segment-clean
research universe, aggregated from the (frozen) minute lake.

Bar convention: ET regular session, **09:30-anchored** buckets — bars start at
09:30, 10:30, …, 15:30 ET (the 15:30 bar is the 30-minute stub to the close).
`timestamp` is the bar START as a true UTC instant (DST-correct via Trino
with_timezone). Per-symbol coverage is clamped to the symbol's clean dominant
segment [min,max] taken from ohlcv_daily, so hourly inherits the EXP-26
ticker-reuse hygiene by construction.

Chunked by calendar year (resumable — years already loaded are skipped);
validation checks that hourly bars roll up EXACTLY to ohlcv_daily's bars
(same minute source ⇒ same daily OHLCV).

  poetry run python scripts/build_ohlcv_hourly.py --create
  poetry run python scripts/build_ohlcv_hourly.py            # load all years
  poetry run python scripts/build_ohlcv_hourly.py --years 2022,2023
  poetry run python scripts/build_ohlcv_hourly.py --validate AAPL,NVDA
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from build_ohlcv_daily import _athena, _splits_lookup  # noqa: E402
from app.config import settings  # noqa: E402
from app.services.equities.adjust import apply_adjustment  # noqa: E402

DB = settings.iceberg_equities_glue_database


def create_table() -> None:
    from app.db.client import get_client
    get_client().command("""
        CREATE TABLE IF NOT EXISTS ohlcv_hourly (
            symbol    LowCardinality(String),
            timestamp DateTime64(3, 'UTC'),
            open Float64, high Float64, low Float64, close Float64, volume Float64,
            version UInt64 DEFAULT toUnixTimestamp64Milli(now64(3))
        ) ENGINE = ReplacingMergeTree(version)
        PARTITION BY toYYYYMM(timestamp) ORDER BY (symbol, timestamp)
    """)
    print("ohlcv_hourly table ready")


def _segment_bounds() -> dict[str, tuple]:
    """Per-symbol clean coverage from ohlcv_daily (the EXP-26 segment ranges)."""
    from app.db.client import get_client
    rows = get_client().query(
        "SELECT symbol, min(toDate(timestamp)), max(toDate(timestamp)) "
        "FROM ohlcv_daily GROUP BY symbol").result_rows
    return {r[0]: (r[1], r[2]) for r in rows}


def _loaded_years() -> set[int]:
    from app.db.client import get_client
    rows = get_client().query(
        "SELECT toYear(timestamp) y, count() FROM ohlcv_hourly GROUP BY y").result_rows
    return {int(r[0]) for r in rows if r[1] > 0}


def _year_query(year: int, bounds: dict[str, tuple]) -> str:
    """One year's hourly aggregation, clamped per symbol to its clean segment.
    The VALUES join carries (symbol, lo, hi); Trino prunes months via the
    timestamp predicate."""
    active = {s: (lo, hi) for s, (lo, hi) in bounds.items()
              if lo.year <= year <= hi.year}
    if not active:
        return ""
    vals = ",".join(f"('{s}', date '{lo}', date '{hi}')"
                    for s, (lo, hi) in sorted(active.items()))
    return f"""
WITH uni(symbol, lo, hi) AS (SELECT * FROM (VALUES {vals}))
SELECT r."symbol",
       date(r."timestamp" AT TIME ZONE 'America/New_York') AS d,
       floor(((hour(r."timestamp" AT TIME ZONE 'America/New_York')*60
             + minute(r."timestamp" AT TIME ZONE 'America/New_York')) - 570) / 60.0) AS bucket,
       min_by(r."open", r."timestamp")  AS open,
       max(r."high")                    AS high,
       min(r."low")                     AS low,
       max_by(r."close", r."timestamp") AS close,
       sum(r."volume")                  AS volume
FROM "{DB}"."polygon_raw" r
JOIN uni ON r."symbol" = uni.symbol
WHERE r."close" > 0
  AND r."timestamp" >= from_iso8601_timestamp('{year}-01-01T00:00:00Z')
  AND r."timestamp" <  from_iso8601_timestamp('{year + 1}-01-06T00:00:00Z')
  AND date(r."timestamp" AT TIME ZONE 'America/New_York') BETWEEN uni.lo AND uni.hi
  AND year(r."timestamp" AT TIME ZONE 'America/New_York') = {year}
  AND (hour(r."timestamp" AT TIME ZONE 'America/New_York')*60
     + minute(r."timestamp" AT TIME ZONE 'America/New_York')) BETWEEN 570 AND 959
GROUP BY 1, 2, 3
"""


def _to_utc_arrow(t):
    """(symbol, d, bucket, o/h/l/c/v) → canonical arrow with DST-correct UTC
    bar-start timestamps (ET wall time 09:30 + bucket hours)."""
    import pandas as pd
    import pyarrow as pa
    df = t.to_pandas()
    wall = (pd.to_datetime(df["d"]) + pd.to_timedelta(570 + df["bucket"].astype(int) * 60,
                                                      unit="m"))
    ts = wall.dt.tz_localize("America/New_York", nonexistent="NaT",
                             ambiguous="NaT").dt.tz_convert("UTC")
    df = df.assign(timestamp=ts).dropna(subset=["timestamp"])
    n = len(df)
    return pa.table({
        "symbol": pa.array(df["symbol"].tolist()),
        "timestamp": pa.array(df["timestamp"].dt.to_pydatetime(), type=pa.timestamp("us", tz="UTC")),
        "open": pa.array(df["open"].astype(float), type=pa.float64()),
        "high": pa.array(df["high"].astype(float), type=pa.float64()),
        "low": pa.array(df["low"].astype(float), type=pa.float64()),
        "close": pa.array(df["close"].astype(float), type=pa.float64()),
        "volume": pa.array(df["volume"].astype(float), type=pa.float64()),
        "vwap": pa.nulls(n, pa.float64()),
        "trade_count": pa.nulls(n, pa.float64()),
    })


def load_year(year: int, bounds: dict[str, tuple], lookup) -> int:
    q = _year_query(year, bounds)
    if not q:
        print(f"  {year}: no symbols in coverage — skipped")
        return 0
    t = _athena(q, timeout_s=900)
    if t.num_rows == 0:
        print(f"  {year}: 0 rows from lake")
        return 0
    adj = apply_adjustment(_to_utc_arrow(t), lookup)
    cols = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]
    df = adj.select(cols).to_pandas()
    df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    from app.db.client import get_client
    cli = get_client()
    rows = df[cols].values.tolist()
    CHUNK = 500_000
    for i in range(0, len(rows), CHUNK):
        cli.insert("ohlcv_hourly", rows[i:i + CHUNK], column_names=cols)
    print(f"  {year}: inserted {len(rows):,} hourly rows "
          f"({df['symbol'].nunique()} symbols)")
    return len(rows)


def validate(symbols: list[str]) -> None:
    """Hourly must roll up EXACTLY to ohlcv_daily (same minute source):
    open of first bar / max high / min low / close of last bar / sum volume."""
    from app.db.client import get_client
    cli = get_client()
    for sym in symbols:
        rows = cli.query("""
            WITH h AS (
                SELECT toDate(timestamp) d,
                       argMin(open, timestamp) o, max(high) hi, min(low) lo,
                       argMax(close, timestamp) c, sum(volume) v
                FROM ohlcv_hourly FINAL WHERE symbol={s:String} GROUP BY d)
            SELECT count(),
                   sum(abs(h.o - d2.open) > 1e-6),
                   sum(abs(h.hi - d2.high) > 1e-6),
                   sum(abs(h.lo - d2.low) > 1e-6),
                   sum(abs(h.c - d2.close) > 1e-6),
                   sum(abs(h.v - d2.volume) > 1)
            FROM h INNER JOIN (
                SELECT toDate(timestamp) d, open, high, low, close, volume
                FROM ohlcv_daily FINAL WHERE symbol={s:String}) d2 USING d
        """, parameters={"s": sym}).result_rows[0]
        n, bo, bh, bl, bc, bv = rows
        ok = (bo or 0) + (bh or 0) + (bl or 0) + (bc or 0) + (bv or 0) == 0
        print(f"  VALIDATE {sym}: {n} overlapping days · mismatches "
              f"o={bo} h={bh} l={bl} c={bc} v={bv} → {'OK ✓' if ok else 'MISMATCH ✗'}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--years", default="", help="comma list; default = all missing")
    ap.add_argument("--validate", default="", help="comma list of symbols")
    a = ap.parse_args(argv)
    if a.create:
        create_table()
    if a.validate:
        validate([s.strip().upper() for s in a.validate.split(",") if s.strip()])
        return 0

    bounds = _segment_bounds()
    if not bounds:
        print("ohlcv_daily is empty — build it first")
        return 1
    lo = min(b[0].year for b in bounds.values())
    hi = max(b[1].year for b in bounds.values())
    wanted = ([int(y) for y in a.years.split(",") if y.strip()] if a.years
              else list(range(lo, hi + 1)))
    done = _loaded_years()
    todo = [y for y in wanted if y not in done]
    skipped = [y for y in wanted if y in done]
    if skipped:
        print(f"already loaded (skipping): {skipped}")
    if not todo:
        print("nothing to do — all requested years loaded")
        return 0
    print(f"loading {len(todo)} year(s): {todo}  ({len(bounds)} symbols)", flush=True)
    lookup = _splits_lookup(sorted(bounds))
    total = 0
    for y in todo:
        total += load_year(y, bounds, lookup)
    print(f"\ndone: {total:,} hourly rows inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
