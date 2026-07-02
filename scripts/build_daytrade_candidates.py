"""
DT-0 — pre-open scanner + 1-minute candidate store for day-trading research.

NO-LOOK-AHEAD INVARIANT: for trading day D, selection uses ONLY
  (a) day D-1's bars (prior-day range/volume/close), and
  (b) day D's PRE-MARKET minutes (04:00–09:29 ET)
— never a regular-session bar of D. The watchlist is what a trader knows at
09:29. Live, the same features come from the Schwab stream/REST pre-open.

Pipeline per year (coarse scan → fine extract):
  1. Athena: per (symbol, ET-day) aggregates split into premarket vs regular
     session (one scan of the year's minute lake).
  2. Locally: join D's premarket with D-1's regular session → scan features
     (gap_pct, pm_dollar_vol, …) → liquidity floor → rank → top-N per day
     → CH `daytrade_scan` (every scanned candidate, features + rank).
  3. Athena: extract the SELECTED (symbol, day) 1m paths (04:00–16:00 ET)
     → CH `ohlcv_1m_candidates` (RAW prices — intraday same-day sim needs no
     split adjustment; cross-day features carry D-1 close in the scan row).

  poetry run python scripts/build_daytrade_candidates.py --create
  poetry run python scripts/build_daytrade_candidates.py --years 2024 --top 30
  poetry run python scripts/build_daytrade_candidates.py --scan-only --years 2024

Liquidity floor (a-priori): prior-day dollar volume ≥ $20M, prior close ≥ $5,
premarket dollar volume ≥ $500k — tradeable names, not lottery tickets.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import pandas as pd  # noqa: E402

from build_ohlcv_daily import _athena  # noqa: E402
from app.config import settings  # noqa: E402

DB = settings.iceberg_equities_glue_database

MIN_PREV_DOLLAR_VOL = 20_000_000.0
MIN_PREV_CLOSE = 5.0
MIN_PM_DOLLAR_VOL = 500_000.0

# Leveraged / inverse / volatility ETPs: mechanical multiples of the real
# mover — we trade the underlying, not the wrapper (same exclusion as the
# EXP-19 momentum pool). Curated list of the liquid offenders.
ETP_EXCLUDE = {
    "TQQQ", "SQQQ", "QLD", "QID", "SSO", "SDS", "SPXL", "SPXS", "UPRO", "SPXU",
    "SOXL", "SOXS", "USD", "SSG", "TNA", "TZA", "UWM", "TWM", "FAS", "FAZ",
    "LABU", "LABD", "CURE", "DRV", "DRN", "ERX", "ERY", "GUSH", "DRIP",
    "NUGT", "DUST", "JNUG", "JDST", "AGQ", "ZSL", "UCO", "SCO", "BOIL", "KOLD",
    "UVXY", "UVIX", "VIXY", "VXX", "SVXY", "SVIX", "VIXM", "VXZ", "XIV",
    "NVDL", "NVDX", "NVDQ", "NVDD", "NVDU", "NVD", "TSLL", "TSLQ", "TSLS",
    "TSLT", "TSLZ", "AMDL", "AMDS", "MSTU", "MSTX", "MSTZ", "SMST", "CONL",
    "AAPU", "AAPD", "GGLL", "GGLS", "AMZU", "AMZD", "METU", "METD", "MSFU",
    "MSFD", "FNGU", "FNGD", "FNGA", "BULZ", "BERZ", "WEBL", "WEBS", "DPST",
    "WANT", "NAIL", "DFEN", "UDOW", "SDOW", "URTY", "SRTY", "TMF", "TMV",
    "TBT", "UBT", "TYD", "TYO", "BITX", "BITU", "SBIT", "ETHU", "ETHD",
    "IBIT", "GBTC", "FBTC", "ETHA", "BITO", "SETH",
    "ETHE", "FETH", "ETHV", "EZET", "BITB", "ARKB", "BTCO", "BRRR", "HODL",
    "BTF", "DEFI", "XBTF", "ETHW",
}


def create_tables() -> None:
    from app.db.client import get_client
    cli = get_client()
    cli.command("""
        CREATE TABLE IF NOT EXISTS daytrade_scan (
            day Date,
            symbol LowCardinality(String),
            rank UInt16,
            gap_pct Float64,
            prev_close Float64, prev_high Float64, prev_low Float64,
            prev_volume Float64, prev_dollar_vol Float64,
            pm_last Float64, pm_high Float64, pm_low Float64,
            pm_volume Float64, pm_dollar_vol Float64,
            version UInt64 DEFAULT toUnixTimestamp64Milli(now64(3))
        ) ENGINE = ReplacingMergeTree(version)
        PARTITION BY toYear(day) ORDER BY (day, rank, symbol)
    """)
    cli.command("""
        CREATE TABLE IF NOT EXISTS ohlcv_1m_candidates (
            symbol LowCardinality(String),
            timestamp DateTime64(3, 'UTC'),
            open Float64, high Float64, low Float64, close Float64, volume Float64,
            version UInt64 DEFAULT toUnixTimestamp64Milli(now64(3))
        ) ENGINE = ReplacingMergeTree(version)
        PARTITION BY toYYYYMM(timestamp) ORDER BY (symbol, timestamp)
    """)
    print("daytrade_scan + ohlcv_1m_candidates tables ready")


def _year_day_aggregates(year: int) -> pd.DataFrame:
    """One Athena scan → per (symbol, ET day): premarket (04:00-09:29) and
    regular-session (09:30-16:00) aggregates."""
    t = _athena(f"""
SELECT "symbol",
       date("timestamp" AT TIME ZONE 'America/New_York') AS d,
       CASE WHEN (hour("timestamp" AT TIME ZONE 'America/New_York')*60
                + minute("timestamp" AT TIME ZONE 'America/New_York')) < 570
            THEN 'pm' ELSE 'rs' END AS sess,
       max_by("close", "timestamp") AS last,
       max("high") AS high, min("low") AS low,
       sum("volume") AS volume,
       sum("close" * "volume") AS dollar_vol
FROM "{DB}"."polygon_raw"
WHERE "close" > 0
  AND "timestamp" >= from_iso8601_timestamp('{year}-01-01T00:00:00Z')
  AND "timestamp" <  from_iso8601_timestamp('{year + 1}-01-06T00:00:00Z')
  AND year("timestamp" AT TIME ZONE 'America/New_York') = {year}
  AND (hour("timestamp" AT TIME ZONE 'America/New_York')*60
     + minute("timestamp" AT TIME ZONE 'America/New_York')) BETWEEN 240 AND 959
GROUP BY 1, 2, 3
""", timeout_s=900)
    df = t.to_pandas()
    df["d"] = pd.to_datetime(df["d"]).dt.date
    return df


def _scan_year(year: int, top_n: int) -> pd.DataFrame:
    """Rank each day's pre-open movers. Returns the top-N rows per day."""
    agg = _year_day_aggregates(year)
    pm = (agg[agg.sess == "pm"]
          .rename(columns={"last": "pm_last", "high": "pm_high", "low": "pm_low",
                           "volume": "pm_volume", "dollar_vol": "pm_dollar_vol"})
          .drop(columns=["sess"])
          .set_index(["symbol", "d"]))
    rs = agg[agg.sess == "rs"].sort_values("d")

    # prior REGULAR session per symbol (shift within symbol; first day of the
    # year uses the year's own history only — the January warmup loses day 1).
    rs = rs.rename(columns={"last": "close"})
    rs["prev_close"] = rs.groupby("symbol")["close"].shift(1)
    rs["prev_high"] = rs.groupby("symbol")["high"].shift(1)
    rs["prev_low"] = rs.groupby("symbol")["low"].shift(1)
    rs["prev_volume"] = rs.groupby("symbol")["volume"].shift(1)
    rs["prev_dollar_vol"] = rs.groupby("symbol")["dollar_vol"].shift(1)
    rs = rs.dropna(subset=["prev_close"])

    j = rs.join(pm, on=["symbol", "d"])
    j = j.dropna(subset=["pm_last"])
    j["gap_pct"] = (j["pm_last"] - j["prev_close"]) / j["prev_close"]

    j = j[(j.prev_dollar_vol >= MIN_PREV_DOLLAR_VOL)
          & (j.prev_close >= MIN_PREV_CLOSE)
          & (j.pm_dollar_vol >= MIN_PM_DOLLAR_VOL)
          & ~j.symbol.isin(ETP_EXCLUDE)]

    # Rank: absolute gap first (the day's story), premarket $ activity second.
    j["score"] = j["gap_pct"].abs() + j["pm_dollar_vol"] / 1e12
    j = j.sort_values(["d", "score"], ascending=[True, False])
    j["rank"] = j.groupby("d").cumcount() + 1
    picked = j[j["rank"] <= top_n]
    print(f"  {year}: {j.d.nunique()} scan days · {len(picked):,} picks "
          f"(median |gap| of picks {picked.gap_pct.abs().median()*100:.1f}%)")
    cols = ["d", "symbol", "rank", "gap_pct", "prev_close", "prev_high", "prev_low",
            "prev_volume", "prev_dollar_vol", "pm_last", "pm_high", "pm_low",
            "pm_volume", "pm_dollar_vol"]
    return picked[cols].rename(columns={"d": "day"})


def _store_scan(df: pd.DataFrame) -> None:
    from app.db.client import get_client
    cols = list(df.columns)
    get_client().insert("daytrade_scan", df[cols].values.tolist(), column_names=cols)
    print(f"  stored {len(df):,} scan rows → daytrade_scan")


def _extract_minutes(picks: pd.DataFrame, year: int) -> None:
    """Pull the selected (symbol, day) 1m paths (04:00–16:00 ET) into CH.
    Chunked VALUES joins keep each Athena query under the size limit."""
    from app.db.client import get_client
    cli = get_client()
    pairs = list(picks[["symbol", "day"]].itertuples(index=False, name=None))
    CHUNK = 2000
    total = 0
    for start in range(0, len(pairs), CHUNK):
        chunk = pairs[start:start + CHUNK]
        vals = ",".join(f"('{s}', date '{d}')" for s, d in chunk)
        t = _athena(f"""
WITH picks(symbol, d) AS (SELECT * FROM (VALUES {vals}))
SELECT r."symbol", r."timestamp", r."open", r."high", r."low", r."close", r."volume"
FROM "{DB}"."polygon_raw" r
JOIN picks ON r."symbol" = picks.symbol
          AND date(r."timestamp" AT TIME ZONE 'America/New_York') = picks.d
WHERE r."close" > 0
  AND r."timestamp" >= from_iso8601_timestamp('{year}-01-01T00:00:00Z')
  AND r."timestamp" <  from_iso8601_timestamp('{year + 1}-01-06T00:00:00Z')
  AND (hour(r."timestamp" AT TIME ZONE 'America/New_York')*60
     + minute(r."timestamp" AT TIME ZONE 'America/New_York')) BETWEEN 240 AND 959
""", timeout_s=900)
        df = t.to_pandas()
        df["timestamp"] = pd.to_datetime(df["timestamp"]).dt.tz_localize(None)
        cols = ["symbol", "timestamp", "open", "high", "low", "close", "volume"]
        rows = df[cols].values.tolist()
        for i in range(0, len(rows), 500_000):
            cli.insert("ohlcv_1m_candidates", rows[i:i + 500_000], column_names=cols)
        total += len(rows)
        print(f"  extracted {len(rows):,} 1m rows ({start + len(chunk)}/{len(pairs)} picks)")
    print(f"  year {year}: {total:,} candidate 1m rows → ohlcv_1m_candidates")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", action="store_true")
    ap.add_argument("--years", default="", help="comma list, e.g. 2024,2025")
    ap.add_argument("--top", type=int, default=30)
    ap.add_argument("--scan-only", action="store_true",
                    help="build daytrade_scan only (skip 1m extraction)")
    a = ap.parse_args(argv)
    if a.create:
        create_tables()
    years = [int(y) for y in a.years.split(",") if y.strip()]
    for y in years:
        print(f"scanning {y}…", flush=True)
        picks = _scan_year(y, a.top)
        if picks.empty:
            print(f"  {y}: no picks (check lake coverage)")
            continue
        _store_scan(picks)
        if not a.scan_only:
            _extract_minutes(picks, y)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
