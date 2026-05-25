#!/usr/bin/env python3
"""
End-to-end pipeline smoke test — operator gate test for the live tier.

Three measured tests covering the full data-flow contract:

  T1 — HOT-CACHE CHART READ
       Pick an existing universe symbol (in CH, in lake). Hit /api/v1/bars
       for a 30-day 1m window. Should return sub-second since data is
       hot in CH.

  T2 — COLD ADD (symbol absent from schwab_universe)
       POST /api/v1/stream with a fresh symbol (not in our 106). Wait
       for the warmup chain to populate CH from:
         a) Schwab tip-fill (last 48d → CH)         expected ~30-60s
         b) lake_to_ch_backfill (~5y polygon → CH)   expected ~3-10min
         c) Schwab daily backfill (~10y → ohlcv_daily) expected ~30s
       Verify all three sources land + report per-source bar counts.

  T3 — SCHWAB 10-YEAR DAILY (F1 path)
       Force a daily backfill for an old-listed symbol (default MSFT).
       Verify ohlcv_daily depth + measure latency.

Each test prints PASS/FAIL with timing. Exits non-zero on any failure
(suitable for CI / cron gate-tests).

Usage:
  poetry run python scripts/validate_pipeline.py
  poetry run python scripts/validate_pipeline.py --hot-symbol AAPL --cold-symbol ARM
  poetry run python scripts/validate_pipeline.py --skip T2 --skip T3  # quick smoke
"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

import requests

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

log = logging.getLogger(__name__)


# Defaults are real, NON-test tickers. Override with CLI flags if your
# universe differs (e.g. T2_DEFAULT cold-add must NOT be in CH already).
T1_DEFAULT_HOT_SYMBOL = "AAPL"     # in 106-universe + lake
T2_DEFAULT_COLD_SYMBOL = "ARM"      # IPO'd Sep 2023, NOT in our seed universe
T3_DEFAULT_OLD_SYMBOL = "MSFT"     # listed 1986, has full Schwab daily history

API_BASE = "http://localhost:8000"
T1_LATENCY_TARGET_MS = 500   # chart read must feel instant
T2_WARMUP_TIMEOUT_S = 600    # 10 min for cold-add full chain
T3_DAILY_TIMEOUT_S = 120     # 2 min for Schwab 10y daily fetch


@dataclass
class TestResult:
    name: str
    passed: bool
    duration_s: float
    details: list[str] = field(default_factory=list)
    failure_reason: str | None = None


# ─────────────────────────────────────────────────────────────────────
# CH helpers (use docker exec to query CH directly — same path operators use)
# ─────────────────────────────────────────────────────────────────────

def ch_query(sql: str) -> str:
    """Run a CH query via docker exec; return stdout stripped."""
    import subprocess
    r = subprocess.run(
        ["docker", "exec", "stockalert_clickhouse", "clickhouse-client",
         "--query", sql],
        capture_output=True, text=True, timeout=30,
    )
    if r.returncode != 0:
        raise RuntimeError(f"CH query failed: {r.stderr.strip()}")
    return r.stdout.strip()


def ch_count(table: str, symbol: str, *, group_by_source: bool = False) -> dict:
    """Return {source: count} for the symbol, or {'_total': count}."""
    if group_by_source:
        rows = ch_query(
            f"SELECT source, count(*) FROM {table} "
            f"WHERE symbol = '{symbol}' GROUP BY source FORMAT TabSeparated"
        )
        out: dict[str, int] = {}
        for line in rows.splitlines():
            if not line.strip():
                continue
            src, n = line.split("\t")
            out[src or "(empty)"] = int(n)
        return out
    n = ch_query(
        f"SELECT count(*) FROM {table} WHERE symbol = '{symbol}'"
    )
    return {"_total": int(n)}


# ─────────────────────────────────────────────────────────────────────
# T1 — Hot-cache chart read
# ─────────────────────────────────────────────────────────────────────

def test_t1_hot_cache(symbol: str) -> TestResult:
    log.info("─── T1: hot-cache chart read for %s (last 30d, 1m) ───", symbol)
    started = time.time()

    # Pre-check: is symbol actually loaded?
    n = int(ch_query(
        f"SELECT count(*) FROM stocks.ohlcv_1m WHERE symbol = '{symbol}' "
        f"AND timestamp >= now() - INTERVAL 30 DAY"
    ))
    if n == 0:
        return TestResult(
            name="T1", passed=False, duration_s=time.time() - started,
            failure_reason=(
                f"{symbol} has 0 bars in CH for the last 30d. Was it loaded? "
                "Run scripts/hotload_ch_from_lake.py --symbols " + symbol
            ),
        )

    api_started = time.time()
    try:
        r = requests.get(
            f"{API_BASE}/api/v1/bars",
            params={"symbol": symbol, "interval": "1m", "lookback_days": 30},
            timeout=10,
        )
        r.raise_for_status()
        bars = r.json()
    except Exception as e:
        return TestResult(
            name="T1", passed=False, duration_s=time.time() - started,
            failure_reason=f"API call failed: {type(e).__name__}: {e}",
        )
    api_ms = (time.time() - api_started) * 1000

    details = [
        f"CH pre-check: {n:,} bars exist for {symbol} in last 30d",
        f"API returned {len(bars):,} bars",
        f"API latency: {api_ms:.0f}ms (target ≤{T1_LATENCY_TARGET_MS}ms)",
    ]
    passed = len(bars) > 0 and api_ms <= T1_LATENCY_TARGET_MS
    failure = None
    if len(bars) == 0:
        failure = "API returned 0 bars (CH has data — likely a reader bug)"
    elif api_ms > T1_LATENCY_TARGET_MS:
        failure = f"latency {api_ms:.0f}ms > target {T1_LATENCY_TARGET_MS}ms"

    return TestResult(
        name="T1", passed=passed, duration_s=time.time() - started,
        details=details, failure_reason=failure,
    )


# ─────────────────────────────────────────────────────────────────────
# T2 — Cold add (symbol not in schwab_universe)
# ─────────────────────────────────────────────────────────────────────

def _wait_for_bars(
    table: str, symbol: str, *, min_bars: int, timeout_s: float,
    poll_interval_s: float = 5.0,
) -> tuple[bool, int, float]:
    """Poll CH until min_bars rows exist for symbol. Returns (ok, count, wall_s)."""
    started = time.time()
    while True:
        n = int(ch_query(
            f"SELECT count(*) FROM {table} WHERE symbol = '{symbol}'"
        ))
        if n >= min_bars:
            return True, n, time.time() - started
        if time.time() - started > timeout_s:
            return False, n, time.time() - started
        time.sleep(poll_interval_s)


def test_t2_cold_add(symbol: str) -> TestResult:
    log.info("─── T2: cold-add %s (warmup chain end-to-end) ───", symbol)
    started = time.time()
    details: list[str] = []

    # Pre-check: ensure symbol is genuinely cold in CH
    n_before = int(ch_query(
        f"SELECT count(*) FROM stocks.ohlcv_1m WHERE symbol = '{symbol}'"
    ))
    if n_before > 0:
        details.append(f"NOTE: {symbol} already has {n_before:,} bars in CH (not truly cold; T2 measures incremental)")

    # Fire the add
    add_started = time.time()
    try:
        r = requests.post(
            f"{API_BASE}/api/v1/stream",
            json={"symbol": symbol},
            timeout=10,
        )
        r.raise_for_status()
    except Exception as e:
        return TestResult(
            name="T2", passed=False, duration_s=time.time() - started,
            failure_reason=f"add POST failed: {type(e).__name__}: {e}",
        )
    add_ms = (time.time() - add_started) * 1000
    details.append(f"POST /api/v1/stream returned in {add_ms:.0f}ms")

    # Wait for tip-fill (Schwab 48d) — first source to land, ~30-60s
    log.info("  Waiting for Schwab tip-fill (~30-60s)...")
    ok_tip, n_tip, wall_tip = _wait_for_bars(
        "stocks.ohlcv_1m", symbol,
        min_bars=n_before + 1000,  # at least 1000 new bars
        timeout_s=120,
    )
    details.append(
        f"Schwab tip-fill: {n_tip - n_before:,} new bars in {wall_tip:.1f}s "
        + ("✓" if ok_tip else "✗")
    )
    if not ok_tip:
        return TestResult(
            name="T2", passed=False, duration_s=time.time() - started,
            details=details, failure_reason="tip-fill timeout (no bars landed in 120s)",
        )

    # Wait for lake→CH backfill (~5y polygon) — second source, ~3-10 min
    log.info("  Waiting for lake→CH (polygon 5y, can take ~3-10min)...")
    ok_lake, n_lake, wall_lake = _wait_for_bars(
        "stocks.ohlcv_1m", symbol,
        min_bars=max(50_000, n_tip * 5),  # significant jump indicates lake load done
        timeout_s=T2_WARMUP_TIMEOUT_S,
        poll_interval_s=15.0,
    )
    details.append(
        f"Lake→CH: total now {n_lake:,} bars in {wall_lake:.1f}s "
        + ("✓" if ok_lake else "✗")
    )

    # Source breakdown (visibility into which paths ran)
    sources = ch_count("stocks.ohlcv_1m", symbol, group_by_source=True)
    src_summary = ", ".join(f"{k}={v:,}" for k, v in sorted(sources.items()))
    details.append(f"Source breakdown: {src_summary}")

    # Verify daily backfill also ran (Schwab 20y → ohlcv_daily)
    n_daily = int(ch_query(
        f"SELECT count(*) FROM stocks.ohlcv_daily WHERE symbol = '{symbol}'"
    ))
    details.append(
        f"Daily backfill (Schwab): {n_daily:,} daily bars in ohlcv_daily "
        + ("✓" if n_daily > 0 else "✗ (warmup chain may not have triggered enqueue_daily — check uvicorn logs)")
    )

    passed = ok_tip and ok_lake and n_daily > 0
    failure = None
    if not ok_lake:
        failure = "lake→CH timeout (5y polygon never landed)"
    elif n_daily == 0:
        failure = "daily backfill did not populate ohlcv_daily"

    return TestResult(
        name="T2", passed=passed, duration_s=time.time() - started,
        details=details, failure_reason=failure,
    )


# ─────────────────────────────────────────────────────────────────────
# T3 — Schwab 10-year daily (F1)
# ─────────────────────────────────────────────────────────────────────

def test_t3_schwab_10y_daily(symbol: str) -> TestResult:
    log.info("─── T3: Schwab 10y daily backfill for %s ───", symbol)
    started = time.time()
    details: list[str] = []

    n_before = int(ch_query(
        f"SELECT count(*) FROM stocks.ohlcv_daily WHERE symbol = '{symbol}'"
    ))
    details.append(f"Daily bars before: {n_before:,}")

    # Force=true bypasses the coverage short-circuit so this is a real test
    try:
        r = requests.post(
            f"{API_BASE}/api/v1/backfill/daily",
            json={"symbols": [symbol], "days": 365 * 10, "force": True},
            timeout=10,
        )
        r.raise_for_status()
    except Exception as e:
        return TestResult(
            name="T3", passed=False, duration_s=time.time() - started,
            details=details,
            failure_reason=f"backfill POST failed: {type(e).__name__}: {e}",
        )
    details.append("Backfill enqueued (force=True)")

    # Poll until count grows or timeout
    log.info("  Waiting for Schwab REST + CH insert (~30-60s)...")
    target_min = max(2500, n_before + 1000)  # expect ~2500 daily bars for 10y
    ok, n_after, wall = _wait_for_bars(
        "stocks.ohlcv_daily", symbol,
        min_bars=target_min,
        timeout_s=T3_DAILY_TIMEOUT_S,
        poll_interval_s=5.0,
    )
    details.append(
        f"After: {n_after:,} bars (delta=+{n_after - n_before:,}) in {wall:.1f}s "
        + ("✓" if ok else "✗")
    )

    # Sanity: date range
    rng = ch_query(
        f"SELECT min(timestamp), max(timestamp) FROM stocks.ohlcv_daily WHERE symbol = '{symbol}'"
    )
    details.append(f"Date range: {rng}")

    passed = ok and n_after >= target_min
    failure = None
    if not ok:
        failure = f"daily count {n_after:,} < target {target_min:,} after {wall:.1f}s"

    return TestResult(
        name="T3", passed=passed, duration_s=time.time() - started,
        details=details, failure_reason=failure,
    )


# ─────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────

TESTS: dict[str, Callable[..., TestResult]] = {
    "T1": test_t1_hot_cache,
    "T2": test_t2_cold_add,
    "T3": test_t3_schwab_10y_daily,
}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument("--hot-symbol", default=T1_DEFAULT_HOT_SYMBOL, help=f"T1 symbol (default: {T1_DEFAULT_HOT_SYMBOL})")
    p.add_argument("--cold-symbol", default=T2_DEFAULT_COLD_SYMBOL, help=f"T2 symbol (default: {T2_DEFAULT_COLD_SYMBOL})")
    p.add_argument("--old-symbol", default=T3_DEFAULT_OLD_SYMBOL, help=f"T3 symbol (default: {T3_DEFAULT_OLD_SYMBOL})")
    p.add_argument("--skip", action="append", default=[], help="Tests to skip (e.g. --skip T2 --skip T3)")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        datefmt="%H:%M:%S",
    )

    results: list[TestResult] = []
    if "T1" not in args.skip:
        results.append(test_t1_hot_cache(args.hot_symbol))
    if "T2" not in args.skip:
        results.append(test_t2_cold_add(args.cold_symbol))
    if "T3" not in args.skip:
        results.append(test_t3_schwab_10y_daily(args.old_symbol))

    # Summary
    log.info("")
    log.info("═══════════════════════════════════════════════════════")
    log.info("              PIPELINE VALIDATION SUMMARY")
    log.info("═══════════════════════════════════════════════════════")
    for r in results:
        status = "✓ PASS" if r.passed else "✗ FAIL"
        log.info("%s  %s  (%.1fs)", status, r.name, r.duration_s)
        for d in r.details:
            log.info("       %s", d)
        if r.failure_reason:
            log.info("       FAILURE: %s", r.failure_reason)
        log.info("")

    n_pass = sum(1 for r in results if r.passed)
    n_fail = len(results) - n_pass
    log.info("  Overall: %d pass, %d fail", n_pass, n_fail)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
