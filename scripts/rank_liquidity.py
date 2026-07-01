"""
Preflight: rank the lake's universe by liquidity (avg daily dollar-volume) so we can
build a LIQUID, delisted-inclusive research universe. Server-side Athena GROUP BY —
returns only the ranked list (cheap output). Checks polygon_raw granularity first to
size the scan window (bounded — cost-aware).

Writes the top-N tickers to configs/liquid_universe.txt for the CH bulk loader.
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import boto3  # noqa: E402
from app.config import settings  # noqa: E402

DB = settings.iceberg_equities_glue_database
REGION = settings.stock_lake_region
OUT = f"s3://{settings.stock_lake_bucket}/athena-results/"
DELISTED_PROBES = {"SIVB", "BBBY", "FRC", "SBNY", "SI", "FISV", "ATVI", "TWTR"}


def run_athena(sql: str, timeout_s: float = 240.0) -> list[list[str]]:
    cli = boto3.client("athena", region_name=REGION)
    qid = cli.start_query_execution(
        QueryString=sql,
        QueryExecutionContext={"Database": DB, "Catalog": "AwsDataCatalog"},
        ResultConfiguration={"OutputLocation": OUT}, WorkGroup="primary",
    )["QueryExecutionId"]
    t0 = time.monotonic()
    while True:
        st = cli.get_query_execution(QueryExecutionId=qid)["QueryExecution"]["Status"]
        state = st["State"]
        if state in {"SUCCEEDED", "FAILED", "CANCELLED"}:
            break
        if time.monotonic() - t0 > timeout_s:
            raise TimeoutError(f"athena timed out after {timeout_s}s")
        time.sleep(1.0)
    if state != "SUCCEEDED":
        raise RuntimeError(f"athena {state}: {st.get('StateChangeReason')}")
    rows, token = [], None
    while True:
        kw = {"QueryExecutionId": qid, "MaxResults": 1000}
        if token:
            kw["NextToken"] = token
        res = cli.get_query_results(**kw)
        for r in res["ResultSet"]["Rows"]:
            rows.append([c.get("VarCharValue", "") for c in r.get("Data", [])])
        token = res.get("NextToken")
        if not token:
            break
    scanned = cli.get_query_execution(QueryExecutionId=qid)["QueryExecution"].get(
        "Statistics", {}).get("DataScannedInBytes", 0)
    print(f"  (scanned {scanned / 1e9:.2f} GB)", flush=True)
    return rows[1:]  # drop header


def main() -> int:
    print(f"lake db={DB} region={REGION}", flush=True)
    # 1. granularity check (tiny bounded query)
    g = run_athena(
        f'SELECT count(*) FROM "{DB}"."polygon_raw" '
        f'WHERE "symbol"=\'AAPL\' AND "timestamp" >= timestamp \'2024-06-03\' '
        f'AND "timestamp" < timestamp \'2024-06-04\'', timeout_s=120)
    per_day = int(g[0][0]) if g and g[0] else 0
    minute = per_day > 10
    print(f"polygon_raw granularity for AAPL/1day = {per_day} rows → {'MINUTE' if minute else 'DAILY'}", flush=True)

    # 2. liquidity ranking over a bounded window (short if minute-grain to cap cost)
    start, end = ("2023-06-01", "2023-12-01") if minute else ("2023-01-01", "2024-01-01")
    print(f"ranking by avg dollar-volume over {start}..{end} …", flush=True)
    rows = run_athena(
        f'SELECT "symbol", avg("close"*"volume") advol, count(*) n '
        f'FROM "{DB}"."polygon_raw" '
        f'WHERE "timestamp" >= timestamp \'{start}\' AND "timestamp" < timestamp \'{end}\' '
        f'AND "close" > 1 '
        f'GROUP BY "symbol" HAVING count(*) > 20 '
        f'ORDER BY advol DESC LIMIT 600', timeout_s=300)
    syms = [r[0] for r in rows if r and r[0]]
    print(f"\nTOP 30 by liquidity: {', '.join(syms[:30])}")
    found = sorted(DELISTED_PROBES & set(syms))
    print(f"\nDELISTED names present in top-600: {found or 'NONE in top-600'}")
    print(f"total ranked symbols: {len(syms)}")
    out = Path("configs/liquid_universe.txt")
    out.write_text(",".join(syms))
    print(f"wrote {len(syms)} tickers → {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
