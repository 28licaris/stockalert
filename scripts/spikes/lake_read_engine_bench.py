"""
Lake read-layer engine benchmark — the spike's decision harness.

Runs each engine (baseline / polars / duckdb / duckdb_iceberg) over a
set of request shapes against the REAL lake and prints a comparison
table: planning ms, compute ms, total ms, peak RSS, row count, and a
content hash. The hashes are then cross-checked PER SHAPE — if two
engines disagree on the dedup output, that is reported as a FAIL
(correctness must hold before speed matters).

Each (engine, shape) runs in a fresh subprocess so peak RSS is isolated
and one engine's import/JIT warmup never pollutes another's numbers.

  AWS_PROFILE=stock-lake poetry run python -m scripts.spikes.lake_read_engine_bench
  AWS_PROFILE=stock-lake poetry run python -m scripts.spikes.lake_read_engine_bench \\
      --symbols AAPL --symbols AAPL,MSFT,NVDA --years 5 \\
      --engines baseline,polars,duckdb

Run this IN-REGION (an EC2/Fargate box in the bucket's region) for
numbers that reflect production cold reads — a laptop over the WAN
measures your home internet, not the engine. The harness prints where
it is running so results aren't misread.

NOTE: this is a spike harness (scripts/spikes/), not a scheduled job.
It is read-only — it never writes to the lake or ClickHouse.
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

ENGINES = ["baseline", "polars", "duckdb", "duckdb_iceberg"]


@dataclass
class Shape:
    label: str
    symbols: str
    start: datetime
    end: datetime
    source_mode: str = "union"


def _utc(y, m, d):
    return datetime(y, m, d, tzinfo=timezone.utc)


def _run_one(engine: str, shape: Shape, repeat: int) -> dict:
    """Spawn a subprocess for one (engine, shape); return best-of-repeat.

    Best-of (min total_ms) trims cold-cache / GC noise; we report the
    engine's achievable latency, not its worst stall. Peak RSS is taken
    from the same best run.
    """
    cmd = [
        sys.executable, "-m", "scripts.spikes.lake_read_engines",
        "--engine", engine,
        "--symbols", shape.symbols,
        "--start", shape.start.isoformat(),
        "--end", shape.end.isoformat(),
        "--source-mode", shape.source_mode,
    ]
    best = None
    for _ in range(repeat):
        proc = subprocess.run(cmd, capture_output=True, text=True)
        line = proc.stdout.strip().splitlines()[-1] if proc.stdout.strip() else ""
        try:
            res = json.loads(line)
        except (json.JSONDecodeError, IndexError):
            res = {"engine": engine, "error": f"no JSON (rc={proc.returncode}): "
                   f"{(proc.stderr or proc.stdout or '').strip()[-300:]}",
                   "rows": None, "hash": None, "total_ms": None, "peak_rss_mb": None}
        if res.get("error"):
            return res
        if best is None or (res.get("total_ms") or 1e18) < (best.get("total_ms") or 1e18):
            best = res
    return best


def _print_table(shape: Shape, results: dict[str, dict]):
    print(f"\n━━ {shape.label}  [{shape.symbols}]  "
          f"{shape.start.date()}→{shape.end.date()}  ({shape.source_mode}) ━━")
    hdr = f"{'engine':<16}{'rows':>9}{'plan ms':>10}{'compute ms':>12}{'total ms':>10}{'peak MB':>9}  notes"
    print(hdr)
    print("─" * len(hdr))
    hashes = {}
    for engine in results:
        r = results[engine]
        if r.get("error"):
            print(f"{engine:<16}{'—':>9}{'—':>10}{'—':>12}{'—':>10}{'—':>9}  ERROR: {r['error'][:60]}")
            continue
        hashes[engine] = r.get("hash")
        notes = []
        if r.get("deletes_present"):
            notes.append("⚠ merge-on-read deletes present (duckdb-on-files may be wrong)")
        print(f"{engine:<16}{r['rows']:>9}{r['planning_ms']:>10}"
              f"{r['compute_ms']:>12}{r['total_ms']:>10}{r['peak_rss_mb']:>9}  {', '.join(notes)}")

    distinct = set(h for h in hashes.values() if h)
    if len(distinct) > 1:
        print("  ❌ CORRECTNESS FAIL — engines disagree on output:")
        for e, h in hashes.items():
            print(f"       {e}: {h}")
    elif distinct:
        print(f"  ✓ all engines agree (hash {distinct.pop()})")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--engines", default=",".join(ENGINES),
                    help="comma list; subset of " + ",".join(ENGINES))
    ap.add_argument("--symbols", action="append", default=None,
                    help="a request's symbol set (repeatable). Default: a sweep.")
    ap.add_argument("--years", type=int, default=5, help="lookback window length")
    ap.add_argument("--repeat", type=int, default=2, help="runs per (engine,shape); best wins")
    args = ap.parse_args(argv)

    engines = [e.strip() for e in args.engines.split(",") if e.strip()]
    end = _utc(2025, 1, 1)
    start = datetime(end.year - args.years, end.month, end.day, tzinfo=timezone.utc)

    if args.symbols:
        shapes = [Shape(f"custom #{i+1}", s, start, end) for i, s in enumerate(args.symbols)]
    else:
        # Default sweep: scale symbol count to probe where Python falls over.
        shapes = [
            Shape("1 symbol", "AAPL", start, end),
            Shape("3 symbols", "AAPL,MSFT,NVDA", start, end),
            Shape("10 symbols", "AAPL,MSFT,NVDA,AMZN,GOOGL,META,TSLA,AMD,NFLX,INTC", start, end),
        ]

    region = os.getenv("STOCK_LAKE_REGION", "us-east-1")
    print(f"host={socket.gethostname()}  AWS_PROFILE={os.getenv('AWS_PROFILE','(unset)')}  "
          f"lake_region={region}  engines={engines}  repeat={args.repeat}")
    print("⚠ run in-region for production-representative numbers" if "AWS_EXECUTION_ENV" not in os.environ
          else "")

    any_fail = False
    for shape in shapes:
        results = {e: _run_one(e, shape, args.repeat) for e in engines}
        _print_table(shape, results)
        hashes = set(r.get("hash") for r in results.values() if r.get("hash"))
        any_fail = any_fail or len(hashes) > 1
    return 1 if any_fail else 0


if __name__ == "__main__":
    sys.exit(main())
