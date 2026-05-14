#!/usr/bin/env python3
"""
Smoke test: read the parquet we just wrote back from S3 and verify the
canonical schema + a sample of data.

The canonical Parquet schema is the contract every future rehydration
tool (lake_verify.sh peek, DuckDB queries, Athena tables) depends on.
This script verifies what we actually wrote matches the spec.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv(ROOT / "scripts" / ".env")

import pandas as pd  # noqa: E402

from app.services.s3_lake_client import S3LakeClient  # noqa: E402


KEY = "raw/provider=polygon-flatfiles/kind=minute/year=2026/date=2026-05-08.parquet"

EXPECTED_MINUTE_COLS = [
    "symbol", "timestamp", "open", "high", "low", "close",
    "volume", "vwap", "trade_count", "source",
]


def main() -> int:
    s3 = S3LakeClient.from_settings()
    print(f"smoke: reading s3://{s3.bucket}/{KEY}")
    body = s3.get_bytes(KEY)
    print(f"  bytes = {len(body)}")

    df = pd.read_parquet(io.BytesIO(body))
    print(f"  rows = {len(df)}")
    print(f"  cols = {list(df.columns)}")

    missing = [c for c in EXPECTED_MINUTE_COLS if c not in df.columns]
    if missing:
        print(f"  ERROR: missing canonical columns: {missing}")
        return 1

    # Spot-check dtypes
    print(f"  dtypes:")
    for c in EXPECTED_MINUTE_COLS:
        print(f"    {c:14s} = {df[c].dtype}")

    # Spot-check tz
    ts = df["timestamp"]
    print(f"  timestamp tz = {ts.dt.tz}")
    if ts.dt.tz is None:
        print("  ERROR: timestamp is naive; expected UTC tz-aware")
        return 2

    print(f"  symbol distinct = {df['symbol'].nunique()}")
    print(f"  symbols seen    = {sorted(df['symbol'].unique())}")
    print(f"  source distinct = {df['source'].unique().tolist()}")
    print(f"  time range      = {ts.min()}  ..  {ts.max()}")

    print(f"\n  sample rows:")
    print(df.head(3).to_string(index=False))

    print("\nsmoke: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
