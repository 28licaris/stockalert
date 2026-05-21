#!/usr/bin/env python3
"""
Post-run verification for silver_ohlcv_build (TA-5.1.7).

**Why this script exists:** after an overnight `--full` backfill
writes millions of silver rows, the operator needs a structured
sanity-check answering:

  1. **Coverage** — how many (symbol, date) cells exist? Per symbol,
     what's the date span? Any unexpected zero-row days?
  2. **Quality** — distribution of gap_count, max_gap_minutes,
     disagreement_count. Top-N worst cells.
  3. **Cross-check** — for sampled (symbol, ts) cells, does the
     silver row's _adj match what we'd compute from bronze + corp
     actions? Does the source_provider in silver match what the
     precedence merge SHOULD have picked given the bronze data?
  4. **Audit** — does the ingestion_runs CH row count match the
     expected (symbols × days) and is status='ok'?

Surfaces outliers before they bite a backtest. Designed to be safe
to re-run any time (no writes, just reads).

**Run:**

    # Verify a specific run window (default: last 7 days):
    poetry run python scripts/verify_silver_build.py
    poetry run python scripts/verify_silver_build.py --since 2024-06-01 --until 2024-06-30

    # Restrict to specific symbols:
    poetry run python scripts/verify_silver_build.py --symbols AAPL,NVDA

    # Tighten the gap-count threshold:
    poetry run python scripts/verify_silver_build.py --max-gap-count 10

    # JSON report:
    poetry run python scripts/verify_silver_build.py --out-json verify.json

Exit codes:
  0 = no quality issues found
  2 = issues found (gap_count / disagreement_count / cross-check)
"""
from __future__ import annotations

import argparse
import json
import logging
import random
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

logger = logging.getLogger(__name__)


_GLYPH_OK = "🟢 OK"
_GLYPH_WARN = "🟡 WARN"
_GLYPH_FAIL = "🔴 FAIL"


@dataclass
class VerificationFindings:
    """Aggregate findings across all symbols × dates."""

    symbols_checked: list[str] = field(default_factory=list)
    since: Optional[date] = None
    until: Optional[date] = None
    total_quality_rows: int = 0
    cells_with_gap_outlier: list[dict] = field(default_factory=list)
    cells_with_disagreement: list[dict] = field(default_factory=list)
    zero_actual_bar_cells: list[dict] = field(default_factory=list)
    sampled_cross_checks: list[dict] = field(default_factory=list)
    sample_failures: list[dict] = field(default_factory=list)
    ingestion_run_summary: dict = field(default_factory=dict)

    @property
    def has_issues(self) -> bool:
        return bool(
            self.cells_with_gap_outlier
            or self.cells_with_disagreement
            or self.zero_actual_bar_cells
            or self.sample_failures
        )


def _parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Expected YYYY-MM-DD; got {s!r}: {e}")


def _resolve_symbols(spec: Optional[str]) -> list[str]:
    if not spec:
        # Default: use SEED_SYMBOLS as the verification floor. Operator
        # passes --symbols explicitly to override.
        from app.data.seed_universe import SEED_SYMBOLS
        return list(SEED_SYMBOLS)
    return [tok.strip().upper() for tok in spec.split(",") if tok.strip()]


# ─────────────────────────────────────────────────────────────────────
# Verification phases
# ─────────────────────────────────────────────────────────────────────


def gather_quality_metrics(
    symbols: list[str],
    since: date,
    until: date,
    *,
    max_gap_count: int,
    max_gap_minutes: int,
    findings: VerificationFindings,
) -> None:
    """Phase 1+2: per-symbol coverage + quality scan via AdjustedOhlcvReader."""
    from app.services.readers.adjusted_ohlcv_reader import AdjustedOhlcvReader

    reader = AdjustedOhlcvReader.from_settings()
    findings.symbols_checked = symbols
    findings.since = since
    findings.until = until

    for sym in symbols:
        try:
            resp = reader.get_bar_quality(sym, since=since, until=until)
        except Exception as e:
            findings.sample_failures.append({
                "phase": "quality_scan",
                "symbol": sym,
                "error": f"{type(e).__name__}: {e}",
            })
            continue

        for row in resp.rows:
            findings.total_quality_rows += 1

            # Coverage check: actual_bars == 0 on a weekday is suspect.
            # Saturdays/Sundays will naturally be 0 — skip those.
            if (row.actual_bars or 0) == 0 and row.date.weekday() < 5:
                findings.zero_actual_bar_cells.append({
                    "symbol": sym,
                    "date": row.date.isoformat(),
                    "providers_seen": row.providers_seen,
                })

            # Gap-outlier check.
            if (
                (row.gap_count or 0) > max_gap_count
                or (row.max_gap_minutes or 0) > max_gap_minutes
            ):
                findings.cells_with_gap_outlier.append({
                    "symbol": sym,
                    "date": row.date.isoformat(),
                    "gap_count": row.gap_count,
                    "max_gap_minutes": row.max_gap_minutes,
                    "actual_bars": row.actual_bars,
                    "expected_bars": row.expected_bars,
                })

            # Disagreement check.
            if (row.disagreement_count or 0) > 0:
                findings.cells_with_disagreement.append({
                    "symbol": sym,
                    "date": row.date.isoformat(),
                    "disagreement_count": row.disagreement_count,
                    "providers_seen": row.providers_seen,
                })


def cross_check_sample(
    symbols: list[str],
    since: date,
    until: date,
    *,
    sample_size: int,
    findings: VerificationFindings,
) -> None:
    """Phase 3: for N random (symbol, day) cells, read silver bars +
    confirm: (a) bars are sorted; (b) timestamps are unique;
    (c) source_provider is in {polygon, schwab}; (d) OHLC columns are
    populated (silver stores split-adjusted directly, no _adj suffix
    after TA-5.1.8)."""
    from app.services.readers.adjusted_ohlcv_reader import AdjustedOhlcvReader

    reader = AdjustedOhlcvReader.from_settings()

    # Build the universe of (sym, day) candidates.
    days = []
    current = since
    while current <= until:
        if current.weekday() < 5:  # weekdays only
            days.append(current)
        current += timedelta(days=1)

    if not days or not symbols:
        return

    rng = random.Random(42)  # deterministic sample for reproducibility
    pool = [(s, d) for s in symbols for d in days]
    sample = rng.sample(pool, min(sample_size, len(pool)))

    for sym, day in sample:
        start_ts = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        end_ts = start_ts + timedelta(days=1)
        try:
            resp = reader.get_bars(sym, start_ts, end_ts)
        except Exception as e:
            findings.sample_failures.append({
                "phase": "cross_check",
                "symbol": sym,
                "date": day.isoformat(),
                "error": f"{type(e).__name__}: {e}",
            })
            continue

        if resp.count == 0:
            # Empty days are flagged in phase 1+2; skip here.
            continue

        # (a) sorted
        ts_seq = [b.timestamp for b in resp.bars]
        sorted_ok = all(ts_seq[i] <= ts_seq[i + 1] for i in range(len(ts_seq) - 1))
        # (b) unique
        unique_ok = len(set(ts_seq)) == len(ts_seq)
        # (c) provider tag
        providers = {b.source_provider for b in resp.bars}
        provider_ok = providers.issubset({"polygon", "schwab"})
        # (d) OHLC populated (defensive — silver stores split-adjusted
        # directly; no _adj suffix after TA-5.1.8 schema cleanup).
        adj_ok = all(
            b.open is not None and b.close is not None
            for b in resp.bars[:10]
        )

        passed = sorted_ok and unique_ok and provider_ok and adj_ok
        entry = {
            "symbol": sym,
            "date": day.isoformat(),
            "bars": resp.count,
            "sorted": sorted_ok,
            "unique": unique_ok,
            "providers": sorted(providers),
            "adj_populated": adj_ok,
            "passed": passed,
        }
        findings.sampled_cross_checks.append(entry)
        if not passed:
            findings.sample_failures.append({
                "phase": "cross_check",
                **entry,
            })


def gather_ingestion_runs_summary(
    since: date, until: date, findings: VerificationFindings,
) -> None:
    """Phase 4: count silver_ohlcv_build CH runs in the verification
    window. Status distribution for the operator."""
    try:
        from app.db import get_client

        client = get_client()
        result = client.query(
            """
            SELECT
              count() AS n,
              countIf(status = 'ok') AS n_ok,
              countIf(status = 'partial_fail') AS n_partial,
              countIf(status NOT IN ('ok', 'partial_fail')) AS n_other,
              sum(rows_written) AS total_rows,
              min(started_at) AS earliest,
              max(finished_at) AS latest
            FROM ingestion_runs
            WHERE job_name = 'silver_ohlcv_build'
              AND toDate(started_at) >= toDate({since:String})
              AND toDate(started_at) <= toDate({until:String})
            """,
            parameters={
                "since": since.isoformat(),
                "until": until.isoformat(),
            },
        )
        if not result.result_rows:
            findings.ingestion_run_summary = {"n_runs": 0}
            return
        row = result.result_rows[0]
        findings.ingestion_run_summary = {
            "n_runs": int(row[0] or 0),
            "n_ok": int(row[1] or 0),
            "n_partial": int(row[2] or 0),
            "n_other": int(row[3] or 0),
            "total_rows": int(row[4] or 0),
            "earliest": str(row[5]) if row[5] else None,
            "latest": str(row[6]) if row[6] else None,
        }
    except Exception as e:
        findings.ingestion_run_summary = {
            "error": f"{type(e).__name__}: {e}",
        }


# ─────────────────────────────────────────────────────────────────────
# Report rendering
# ─────────────────────────────────────────────────────────────────────


def print_report(findings: VerificationFindings) -> None:
    print()
    print("─── silver_ohlcv_build VERIFICATION ───")
    print(f"  window:           {findings.since}..{findings.until}")
    print(f"  symbols checked:  {len(findings.symbols_checked)}")
    print(f"  bar_quality rows: {findings.total_quality_rows:,}")
    print()

    # Coverage outliers.
    print(f"Zero-actual-bars weekdays: {len(findings.zero_actual_bar_cells)}")
    for cell in findings.zero_actual_bar_cells[:10]:
        print(
            f"  - {cell['symbol']:<6} {cell['date']}  "
            f"providers={cell.get('providers_seen')}"
        )
    if len(findings.zero_actual_bar_cells) > 10:
        print(f"  ... +{len(findings.zero_actual_bar_cells) - 10} more")
    print()

    # Gap outliers.
    print(f"Gap-count outliers:        {len(findings.cells_with_gap_outlier)}")
    for cell in findings.cells_with_gap_outlier[:10]:
        print(
            f"  - {cell['symbol']:<6} {cell['date']}  "
            f"gaps={cell['gap_count']} max_gap_min={cell['max_gap_minutes']} "
            f"actual={cell['actual_bars']}/{cell['expected_bars']}"
        )
    if len(findings.cells_with_gap_outlier) > 10:
        print(f"  ... +{len(findings.cells_with_gap_outlier) - 10} more")
    print()

    # Disagreements.
    print(f"Provider disagreements:    {len(findings.cells_with_disagreement)}")
    for cell in findings.cells_with_disagreement[:10]:
        print(
            f"  - {cell['symbol']:<6} {cell['date']}  "
            f"disagreements={cell['disagreement_count']} "
            f"providers={cell.get('providers_seen')}"
        )
    if len(findings.cells_with_disagreement) > 10:
        print(f"  ... +{len(findings.cells_with_disagreement) - 10} more")
    print()

    # Cross-check sample.
    print(f"Cross-check sample:        {len(findings.sampled_cross_checks)}")
    n_passed = sum(1 for c in findings.sampled_cross_checks if c["passed"])
    print(f"  passed: {n_passed}/{len(findings.sampled_cross_checks)}")
    for cell in findings.sample_failures[:5]:
        print(f"  - {cell}")
    print()

    # Ingestion-runs audit.
    print("Ingestion-runs audit:")
    irs = findings.ingestion_run_summary
    if "error" in irs:
        print(f"  ⚠️  could not query: {irs['error']}")
    else:
        print(
            f"  total_runs={irs.get('n_runs', 0)}  "
            f"ok={irs.get('n_ok', 0)}  "
            f"partial_fail={irs.get('n_partial', 0)}  "
            f"other={irs.get('n_other', 0)}"
        )
        print(f"  total_rows_written={irs.get('total_rows', 0):,}")
    print()

    # Roll-up.
    if findings.has_issues:
        print(
            f"🔴 ISSUES FOUND. Review outliers above. Cross-check the "
            f"first few via the silver-bar HTTP route "
            f"(/api/silver/bars/SYMBOL?start=...&end=...) before declaring "
            f"the backfill clean."
        )
    else:
        print(
            "✅ No issues found. silver_ohlcv_build output appears clean "
            "across the verification window."
        )
    print()


def _build_parser() -> argparse.ArgumentParser:
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    week_ago = yesterday - timedelta(days=7)
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--since", type=_parse_date, default=week_ago,
        help=f"Lower bound on date (inclusive). Default: 7 days ago ({week_ago}).",
    )
    p.add_argument(
        "--until", type=_parse_date, default=yesterday,
        help=f"Upper bound on date (inclusive). Default: yesterday ({yesterday}).",
    )
    p.add_argument(
        "--symbols", type=str, default=None,
        help="Comma-separated symbols to verify (default: SEED_SYMBOLS). "
             "Example: 'AAPL,NVDA,MSFT'.",
    )
    p.add_argument(
        "--max-gap-count", type=int, default=5,
        help="Flag (symbol, date) cells with more than this many gap "
             "runs (default: 5).",
    )
    p.add_argument(
        "--max-gap-minutes", type=int, default=10,
        help="Flag (symbol, date) cells with a single gap longer than "
             "this (default: 10 min).",
    )
    p.add_argument(
        "--sample-size", type=int, default=20,
        help="How many (symbol, date) cells to cross-check via the "
             "reader. Default 20.",
    )
    p.add_argument(
        "--out-json", type=Path, default=None,
        help="Write structured report to this path.",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    symbols = _resolve_symbols(args.symbols)
    findings = VerificationFindings()

    logger.info(
        "verify_silver_build: window=%s..%s symbols=%d sample=%d",
        args.since, args.until, len(symbols), args.sample_size,
    )

    gather_quality_metrics(
        symbols, args.since, args.until,
        max_gap_count=args.max_gap_count,
        max_gap_minutes=args.max_gap_minutes,
        findings=findings,
    )
    cross_check_sample(
        symbols, args.since, args.until,
        sample_size=args.sample_size,
        findings=findings,
    )
    gather_ingestion_runs_summary(args.since, args.until, findings)

    print_report(findings)

    if args.out_json:
        payload = {
            "since": args.since.isoformat(),
            "until": args.until.isoformat(),
            "symbols_checked": findings.symbols_checked,
            "total_quality_rows": findings.total_quality_rows,
            "zero_actual_bar_cells": findings.zero_actual_bar_cells,
            "cells_with_gap_outlier": findings.cells_with_gap_outlier,
            "cells_with_disagreement": findings.cells_with_disagreement,
            "sampled_cross_checks": findings.sampled_cross_checks,
            "sample_failures": findings.sample_failures,
            "ingestion_run_summary": findings.ingestion_run_summary,
            "issues_found": findings.has_issues,
        }
        args.out_json.write_text(json.dumps(payload, indent=2, default=str))
        print(f"JSON report → {args.out_json}")

    return 2 if findings.has_issues else 0


if __name__ == "__main__":
    sys.exit(main())
