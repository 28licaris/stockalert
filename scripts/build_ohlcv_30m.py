"""
Materialize CH `ohlcv_30m` — split-ADJUSTED 30-minute bars for the segment-clean
research universe, aggregated from the (frozen) minute lake.

Sibling of `build_ohlcv_hourly.py` and deliberately identical in convention:
**ET regular session, 09:30-anchored**. 6.5h / 30m divides exactly, so a session
is 13 bars (09:30, 10:00, …, 15:30) with no stub — unlike hourly, whose 15:30
bar is a half-bar. `timestamp` is the bar START as a true UTC instant
(DST-correct via Trino with_timezone). Per-symbol coverage is clamped to the
symbol's clean dominant segment [min,max] from ohlcv_daily, so 30m inherits the
EXP-26 ticker-reuse hygiene by construction.

Built for the PPL PIT-calibration harness (`app/services/ppl/calibrate.py`,
spec P2/P3), which needs intraday depth the live ClickHouse tier does not have.

> **Session convention matters here.** This table is RTH-only, matching
> ohlcv_daily and ohlcv_hourly. The live chart path resamples CH `ohlcv_1m`,
> which carries the full ET 04:00-20:00 day (32 bars per session at 30m). A
> calibration run against this table only describes the RTH rendering — see
> the PPL spec §6 and `_load_bars`' `rth_only` handling.

Chunked by calendar year (resumable — years already loaded are skipped);
validation checks that 30m bars roll up EXACTLY to ohlcv_hourly AND to
ohlcv_daily (same minute source ⇒ same OHLCV).

  poetry run python scripts/build_ohlcv_30m.py --create
  poetry run python scripts/build_ohlcv_30m.py            # load all years
  poetry run python scripts/build_ohlcv_30m.py --years 2022,2023
  poetry run python scripts/build_ohlcv_30m.py --validate AAPL,NVDA
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

# ET regular session in minutes-from-midnight: 09:30 (570) through 15:59 (959).
SESSION_OPEN_MIN = 570
SESSION_LAST_MIN = 959
BAR_MINUTES = 30
BARS_PER_SESSION = (SESSION_LAST_MIN + 1 - SESSION_OPEN_MIN) // BAR_MINUTES  # 13


def create_table() -> None:
    from app.db.client import get_client
    get_client().command("""
        CREATE TABLE IF NOT EXISTS ohlcv_30m (
            symbol    LowCardinality(String),
            timestamp DateTime64(3, 'UTC'),
            open Float64, high Float64, low Float64, close Float64, volume Float64,
            version UInt64 DEFAULT toUnixTimestamp64Milli(now64(3))
        ) ENGINE = ReplacingMergeTree(version)
        PARTITION BY toYYYYMM(timestamp) ORDER BY (symbol, timestamp)
    """)
    print("ohlcv_30m table ready")


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
        "SELECT toYear(timestamp) y, count() FROM ohlcv_30m GROUP BY y").result_rows
    return {int(r[0]) for r in rows if r[1] > 0}


def _year_query(year: int, bounds: dict[str, tuple]) -> str:
    """One year's 30-minute aggregation, clamped per symbol to its clean segment.
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
             + minute(r."timestamp" AT TIME ZONE 'America/New_York'))
             - {SESSION_OPEN_MIN}) / {BAR_MINUTES}.0) AS bucket,
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
     + minute(r."timestamp" AT TIME ZONE 'America/New_York'))
      BETWEEN {SESSION_OPEN_MIN} AND {SESSION_LAST_MIN}
GROUP BY 1, 2, 3
"""


def _to_utc_arrow(t):
    """(symbol, d, bucket, o/h/l/c/v) → canonical arrow with DST-correct UTC
    bar-start timestamps (ET wall time 09:30 + bucket × 30m)."""
    import pandas as pd
    import pyarrow as pa
    df = t.to_pandas()
    wall = (pd.to_datetime(df["d"])
            + pd.to_timedelta(SESSION_OPEN_MIN + df["bucket"].astype(int) * BAR_MINUTES,
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
    t = _athena(q, timeout_s=1200)
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
        cli.insert("ohlcv_30m", rows[i:i + CHUNK], column_names=cols)
    print(f"  {year}: inserted {len(rows):,} 30m rows "
          f"({df['symbol'].nunique()} symbols)")
    return len(rows)


def validate(symbols: list[str]) -> None:
    """30m must roll up EXACTLY to both coarser tiers (same minute source):
    open of first bar / max high / min low / close of last bar / sum volume.

    Checking BOTH catches a bucket-boundary error that a daily-only check would
    miss — daily aggregates the whole session, so a misaligned intra-session
    boundary still sums to the right day.
    """
    from app.db.client import get_client
    cli = get_client()
    for sym in symbols:
        for coarser, grain in (("ohlcv_daily", "toDate(timestamp)"),
                               ("ohlcv_hourly", "toStartOfHour(timestamp)")):
            # Hourly is 09:30-anchored, so its buckets are :30-offset from the
            # clock hour; group the 30m bars by the hourly bar they belong to.
            key = ("toDate(timestamp)" if coarser == "ohlcv_daily"
                   else "toStartOfInterval(timestamp - toIntervalMinute(30), "
                        "toIntervalHour(1)) + toIntervalMinute(30)")
            rows = cli.query(f"""
                WITH m AS (
                    SELECT {key} k,
                           argMin(open, timestamp) o, max(high) hi, min(low) lo,
                           argMax(close, timestamp) c, sum(volume) v
                    FROM ohlcv_30m FINAL WHERE symbol={{s:String}} GROUP BY k)
                SELECT count(),
                       sum(abs(m.o - x.open) > 1e-6),
                       sum(abs(m.hi - x.high) > 1e-6),
                       sum(abs(m.lo - x.low) > 1e-6),
                       sum(abs(m.c - x.close) > 1e-6),
                       sum(abs(m.v - x.volume) > 1)
                FROM m INNER JOIN (
                    SELECT {grain} k, open, high, low, close, volume
                    FROM {coarser} FINAL WHERE symbol={{s:String}}) x USING k
            """, parameters={"s": sym}).result_rows[0]
            n, bo, bh, bl, bc, bv = rows
            ok = (bo or 0) + (bh or 0) + (bl or 0) + (bc or 0) + (bv or 0) == 0
            print(f"  VALIDATE {sym} vs {coarser}: {n} overlapping buckets · "
                  f"mismatches o={bo} h={bh} l={bl} c={bc} v={bv} "
                  f"→ {'OK ✓' if ok else 'MISMATCH ✗'}")
            if n == 0:
                print(f"    ⚠ no overlap with {coarser} — nothing was actually checked")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--years", default="", help="comma list; default = all missing")
    ap.add_argument("--validate", default="", help="comma list of symbols")
    ap.add_argument("--symbols", default="", help="restrict the load to these symbols")
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
    if a.symbols:
        want = {s.strip().upper() for s in a.symbols.split(",") if s.strip()}
        missing = want - bounds.keys()
        if missing:
            print(f"⚠ not in ohlcv_daily coverage, skipping: {sorted(missing)}")
        bounds = {s: b for s, b in bounds.items() if s in want}
        if not bounds:
            print("no requested symbol is in ohlcv_daily coverage")
            return 1

    lo = min(b[0].year for b in bounds.values())
    hi = max(b[1].year for b in bounds.values())
    wanted = ([int(y) for y in a.years.split(",") if y.strip()] if a.years
              else list(range(lo, hi + 1)))
    done = _loaded_years() if not a.symbols else set()
    todo = [y for y in wanted if y not in done]
    skipped = [y for y in wanted if y in done]
    if skipped:
        print(f"already loaded (skipping): {skipped}")
    if not todo:
        print("nothing to do — all requested years loaded")
        return 0
    print(f"loading {len(todo)} year(s): {todo}  ({len(bounds)} symbols, "
          f"{BARS_PER_SESSION} bars/session)", flush=True)
    lookup = _splits_lookup(sorted(bounds))
    total = 0
    for y in todo:
        total += load_year(y, bounds, lookup)
    print(f"\ndone: {total:,} 30m rows inserted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
