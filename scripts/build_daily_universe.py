"""
Build the LIQUID, delisted-inclusive DAILY research universe.

Step 1 (this script): rank the lake's full history by lifetime avg dollar-volume
(server-side Athena), take the top-N (incl. delisted — names whose max(ts) is in the
past), and write the universe list. Cost-bounded: one aggregate scan, ranked output.

  poetry run python scripts/build_daily_universe.py --top 500
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3  # noqa: E402
from app.config import settings  # noqa: E402

DB = settings.iceberg_equities_glue_database
REGION = settings.stock_lake_region
OUT = f"s3://{settings.stock_lake_bucket}/athena-results/"


def run_athena(sql: str, timeout_s: float = 420.0) -> list[list[str]]:
    cli = boto3.client("athena", region_name=REGION)
    qid = cli.start_query_execution(
        QueryString=sql, QueryExecutionContext={"Database": DB, "Catalog": "AwsDataCatalog"},
        ResultConfiguration={"OutputLocation": OUT}, WorkGroup="primary")["QueryExecutionId"]
    t0 = time.monotonic()
    while True:
        st = cli.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]["State"]
        if st in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            break
        if time.monotonic() - t0 > timeout_s:
            raise TimeoutError(f"athena timed out after {timeout_s}s")
        time.sleep(2.0)
    if st != "SUCCEEDED":
        raise RuntimeError(f"athena {st}")
    rows, token = [], None
    while True:
        kw = {"QueryExecutionId": qid, "MaxResults": 1000}
        if token:
            kw["NextToken"] = token
        res = cli.get_query_results(**kw)
        rows += [[c.get("VarCharValue", "") for c in r.get("Data", [])] for r in res["ResultSet"]["Rows"]]
        token = res.get("NextToken")
        if not token:
            break
    gb = cli.get_query_execution(QueryExecutionId=qid)["QueryExecution"].get("Statistics", {}).get("DataScannedInBytes", 0) / 1e9
    print(f"  (scanned {gb:.1f} GB)", flush=True)
    return rows[1:]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=500)
    a = ap.parse_args(argv)
    print(f"ranking lake full history by lifetime avg dollar-volume, top {a.top}…", flush=True)
    rows = run_athena(
        f'SELECT "symbol", avg("close"*"volume") advol, count(*) n, '
        f'min("timestamp") lo, max("timestamp") hi '
        f'FROM "{DB}"."polygon_raw" WHERE "close" > 1 '
        f'GROUP BY "symbol" HAVING count(*) > 2000 ORDER BY advol DESC LIMIT {a.top}')
    syms = [r[0] for r in rows if r and r[0]]
    # delisted = last bar well before "now" (data ends ~2026-06)
    delisted = [r[0] for r in rows if len(r) >= 5 and r[4] and r[4] < "2026-01-01"]
    print(f"\ntop {len(syms)} liquid names. sample: {', '.join(syms[:25])}")
    print(f"delisted/renamed in set (last bar <2026): {len(delisted)} → {', '.join(delisted[:20])}")
    Path("configs/liquid_universe.txt").write_text(",".join(syms))
    print(f"wrote {len(syms)} → configs/liquid_universe.txt")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
