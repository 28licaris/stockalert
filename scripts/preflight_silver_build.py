#!/usr/bin/env python3
"""
Pre-flight check for silver_ohlcv_build (TA-5.1.7).

**Why this script exists:** before kicking off an overnight `--full`
backfill that could take hours, an operator needs ~30 seconds of
confidence that every wire in the silver-build pipeline is sound.
This script:

  1. Pings the Iceberg catalog (Glue reachable + warehouse bucket set)
  2. Loads bronze.polygon_minute + bronze.schwab_minute, reports row counts
  3. Loads silver.corp_actions, reports the split-factor index size
     (or WARNs if absent — silver will run with F=1 for every symbol)
  4. Ensures silver.ohlcv_1m + silver.bar_quality (creates if missing)
  5. Runs ONE end-to-end build slice (--symbol --day, defaults
     AAPL × yesterday) — full pipeline read→normalize→merge→upsert
  6. Reads back the just-written silver rows + bar_quality row,
     reports counts + key metrics
  7. Checks the CH `ingestion_runs` audit row was recorded

If all 7 pass, the operator can safely kick off `--full`. If any
fail, the message tells them exactly what to fix.

**Run:**

    poetry run python scripts/preflight_silver_build.py
    poetry run python scripts/preflight_silver_build.py --symbol NVDA
    poetry run python scripts/preflight_silver_build.py --symbol NVDA --day 2024-06-10
    poetry run python scripts/preflight_silver_build.py --out-json preflight.json

Exit codes:
  0 = all checks passed (safe to run --full)
  2 = one or more checks failed (do not run --full)
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
import traceback
from dataclasses import asdict, dataclass, field
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
class CheckResult:
    name: str
    status: str  # "ok" | "warn" | "fail"
    message: str = ""
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def glyph(self) -> str:
        return {"ok": _GLYPH_OK, "warn": _GLYPH_WARN, "fail": _GLYPH_FAIL}.get(
            self.status, self.status,
        )


def _parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"Expected ISO date YYYY-MM-DD; got {s!r}: {e}"
        )


# ─────────────────────────────────────────────────────────────────────
# Individual checks
# ─────────────────────────────────────────────────────────────────────


def check_catalog_reachable() -> CheckResult:
    """1: Iceberg catalog (Glue) reachable."""
    try:
        from app.services.iceberg_catalog import get_catalog
        from app.config import settings

        cat = get_catalog()
        ns_list = cat.list_namespaces()
        return CheckResult(
            name="catalog_reachable",
            status="ok",
            message=(
                f"Glue catalog reachable; "
                f"warehouse=s3://{settings.stock_lake_bucket}/"
                f"{settings.iceberg_warehouse_prefix} "
                f"({len(ns_list)} namespaces)"
            ),
            detail={"namespaces": [".".join(n) for n in ns_list]},
        )
    except Exception as e:
        return CheckResult(
            name="catalog_reachable",
            status="fail",
            message=(
                "Iceberg catalog unreachable. Check STOCK_LAKE_BUCKET, "
                "ICEBERG_GLUE_DATABASE, ICEBERG_WAREHOUSE_PREFIX, and AWS creds."
            ),
            detail={"error": f"{type(e).__name__}: {e}"},
        )


def check_bronze_minute_tables() -> CheckResult:
    """2: bronze.polygon_minute + bronze.schwab_minute exist + have rows."""
    try:
        from app.services.bronze.schemas import bronze_table_id
        from app.services.iceberg_catalog import get_catalog

        cat = get_catalog()
        per_table: dict[str, Any] = {}
        for short in ("polygon_minute", "schwab_minute"):
            try:
                tbl = cat.load_table(bronze_table_id(short))
                snap = tbl.current_snapshot()
                if snap is None:
                    per_table[short] = {"row_count": 0, "snapshot": None}
                    continue
                summary = snap.summary or {}
                # Pull total-records (Iceberg snapshot summary key).
                summary_map = (
                    summary.additional_properties
                    if hasattr(summary, "additional_properties") else dict(summary)
                )
                per_table[short] = {
                    "row_count": int(summary_map.get("total-records", 0)),
                    "snapshot_id": str(snap.snapshot_id),
                }
            except Exception as e:
                per_table[short] = {"error": f"{type(e).__name__}: {e}"}

        # FAIL if any table is missing or empty; WARN if Schwab is
        # empty but Polygon is healthy (acceptable on day-zero before
        # the first Schwab nightly).
        polygon = per_table.get("polygon_minute", {})
        schwab = per_table.get("schwab_minute", {})

        if "error" in polygon and "error" in schwab:
            return CheckResult(
                name="bronze_minute_tables",
                status="fail",
                message="Both bronze minute tables unreachable.",
                detail=per_table,
            )

        polygon_rows = polygon.get("row_count", 0)
        schwab_rows = schwab.get("row_count", 0)

        if polygon_rows == 0 and schwab_rows == 0:
            return CheckResult(
                name="bronze_minute_tables",
                status="fail",
                message=(
                    "Both bronze tables are empty. Run the nightly "
                    "polygon/schwab refresh first (or the bulk backfill "
                    "scripts) — silver_build has no source data."
                ),
                detail=per_table,
            )

        if schwab_rows == 0:
            return CheckResult(
                name="bronze_minute_tables",
                status="warn",
                message=(
                    f"bronze.polygon_minute has {polygon_rows:,} rows; "
                    "bronze.schwab_minute is empty. Silver will build "
                    "from Polygon alone (precedence merge degenerates "
                    "to single-provider). Schwab nightly refresh "
                    "should fill this within 24h."
                ),
                detail=per_table,
            )

        return CheckResult(
            name="bronze_minute_tables",
            status="ok",
            message=(
                f"polygon={polygon_rows:,} rows, "
                f"schwab={schwab_rows:,} rows"
            ),
            detail=per_table,
        )
    except Exception as e:
        return CheckResult(
            name="bronze_minute_tables",
            status="fail",
            message=f"Unexpected error loading bronze tables: {e}",
            detail={"traceback": traceback.format_exc()},
        )


def check_silver_corp_actions() -> CheckResult:
    """3: silver.corp_actions is present (else silver _adj = _raw)."""
    try:
        from app.services.iceberg_catalog import get_catalog
        from app.services.silver.schemas import silver_table_id

        cat = get_catalog()
        try:
            tbl = cat.load_table(silver_table_id("corp_actions"))
        except Exception:
            return CheckResult(
                name="silver_corp_actions",
                status="warn",
                message=(
                    "silver.corp_actions does NOT exist. silver_ohlcv_build "
                    "will run with empty split index — _adj columns will "
                    "equal _raw (no adjustment applied). Run "
                    "scripts/run_corp_actions_backfill.py --full first."
                ),
                detail={},
            )

        snap = tbl.current_snapshot()
        if snap is None:
            return CheckResult(
                name="silver_corp_actions",
                status="warn",
                message=(
                    "silver.corp_actions exists but is empty (no snapshot). "
                    "Run scripts/run_corp_actions_backfill.py --full first."
                ),
                detail={},
            )

        summary = snap.summary
        summary_map = (
            summary.additional_properties
            if hasattr(summary, "additional_properties") else dict(summary)
        )
        rows = int(summary_map.get("total-records", 0))
        if rows == 0:
            return CheckResult(
                name="silver_corp_actions",
                status="warn",
                message="silver.corp_actions has 0 rows; no split adjustments will be applied.",
                detail={"row_count": 0},
            )

        return CheckResult(
            name="silver_corp_actions",
            status="ok",
            message=f"silver.corp_actions has {rows:,} rows",
            detail={"row_count": rows, "snapshot_id": str(snap.snapshot_id)},
        )
    except Exception as e:
        return CheckResult(
            name="silver_corp_actions",
            status="fail",
            message=f"Unexpected error: {e}",
            detail={"traceback": traceback.format_exc()},
        )


def check_corp_actions_year_coverage() -> CheckResult:
    """3b: corp_actions year-by-year coverage matches BRONZE_HISTORY_START..today.

    **Why this check exists:** TA-5.0 live verification (2026-05-17)
    found Yahoo spot-checks on TSLA/AMZN/GOOGL returning RAW prices
    instead of adjusted ones. Root cause: bronze.polygon_corp_actions
    had only 5,108 rows total — entire years were missing or partially
    backfilled, so post-split bars saw factor=1 in those windows.

    Silver --full will silently produce wrong adjusted prices for any
    bar whose downstream split is missing. This check catches the
    failure mode BEFORE the multi-hour silver build runs:

      - Reads BRONZE_HISTORY_START as the lower bound.
      - Reads today's UTC date as the upper bound.
      - For every full calendar year in [start..today-1], expects at
        least `_MIN_ROWS_PER_FULL_YEAR` rows.
      - Reports per-year row counts; FAILs if any full year is below
        threshold; WARNs if the current year is under-filled (expected
        during partial-year periods).

    Both bronze.polygon_corp_actions AND silver.corp_actions are
    checked — the silver build reads silver, but the bronze gap is
    what we typically need to fix first.
    """
    try:
        import collections

        from app.config import settings
        from app.services.bronze.schemas import bronze_table_id
        from app.services.iceberg_catalog import get_catalog
        from app.services.silver.schemas import silver_table_id

        # Lower bound from BRONZE_HISTORY_START, upper bound = today UTC.
        try:
            start = date.fromisoformat(settings.bronze_history_start)
        except (TypeError, ValueError):
            start = date(2021, 1, 4)
        today = datetime.now(timezone.utc).date()
        # If we have the current year only partially, treat the current
        # year as a WARN-only band, not a FAIL.
        expected_full_years = list(range(start.year, today.year))
        all_years = list(range(start.year, today.year + 1))

        # Minimum rows for a "fully covered" calendar year. Real data
        # ranges 30K-200K rows/year (splits + dividends). 5K is a
        # generous lower bound that catches the 5,108-row truncation
        # we saw without false-positiving early years (2003 had 3,911).
        # Skip the floor check for 2003 since Polygon's earliest data
        # is mid-2003 (~half-year).
        _MIN_ROWS_PER_FULL_YEAR = 5_000

        cat = get_catalog()
        gaps: dict[str, dict] = {}
        per_table_detail: dict[str, dict] = {}

        for short, label in (
            ("polygon_corp_actions", "bronze.polygon_corp_actions"),
            # silver.corp_actions is checked too — but if bronze is
            # under-filled, silver inherits the gap.
        ):
            try:
                tbl = cat.load_table(bronze_table_id(short))
            except Exception as e:
                gaps[label] = {"error": f"{type(e).__name__}: {e}"}
                continue

            # Per-year row counts via Arrow scan. Only pulls `ex_date`.
            try:
                arrow = tbl.scan(selected_fields=["ex_date"]).to_arrow()
            except TypeError:
                # Older PyIceberg without `selected_fields` kwarg.
                arrow = tbl.scan().to_arrow().select(["ex_date"])

            counts = collections.Counter()
            for d in arrow.column("ex_date").to_pylist():
                if d is None:
                    continue
                if start.year <= d.year <= today.year:
                    counts[d.year] += 1
            per_year = {y: int(counts.get(y, 0)) for y in all_years}
            per_table_detail[label] = per_year

            year_gaps: list[int] = []
            for y in expected_full_years:
                # Skip 2003 — Polygon's earliest data is mid-year.
                if y == 2003:
                    continue
                if per_year.get(y, 0) < _MIN_ROWS_PER_FULL_YEAR:
                    year_gaps.append(y)
            if year_gaps:
                gaps[label] = {"under_threshold_years": year_gaps}

        # Decide overall status.
        if gaps:
            # Find the union of bad years across tables.
            bad_year_set: set[int] = set()
            for info in gaps.values():
                bad_year_set.update(info.get("under_threshold_years", []))
            if bad_year_set:
                bad_str = ", ".join(str(y) for y in sorted(bad_year_set))
                fix_cmd = (
                    f"  poetry run python scripts/run_corp_actions_backfill.py \\\n"
                    f"    --since {min(bad_year_set)}-01-01 \\\n"
                    f"    --until {max(bad_year_set)}-12-31 --bronze-only"
                )
                return CheckResult(
                    name="corp_actions_year_coverage",
                    status="fail",
                    message=(
                        f"corp_actions under-filled for year(s) {bad_str} "
                        f"(< {_MIN_ROWS_PER_FULL_YEAR:,} rows). silver "
                        "--full would produce WRONG adjusted prices for "
                        "bars whose downstream splits fall in those years. "
                        f"Fix:\n{fix_cmd}\n"
                        "(then drop+rebuild silver.corp_actions before silver --full)"
                    ),
                    detail={"per_table": per_table_detail, "gaps": gaps},
                )
            else:
                # Some other error path (table missing).
                return CheckResult(
                    name="corp_actions_year_coverage",
                    status="fail",
                    message=f"corp_actions coverage check errored: {gaps}",
                    detail={"per_table": per_table_detail, "gaps": gaps},
                )

        # Current year coverage = WARN if < 1000 rows (Polygon
        # populates from announcement_date, so a fresh year takes a
        # few weeks to accumulate; this is informational only).
        cy = today.year
        cy_rows = per_table_detail.get("bronze.polygon_corp_actions", {}).get(cy, 0)
        if cy_rows < 100:
            return CheckResult(
                name="corp_actions_year_coverage",
                status="warn",
                message=(
                    f"All historical years have ≥ {_MIN_ROWS_PER_FULL_YEAR:,} "
                    f"rows, but current year {cy} has only {cy_rows} rows "
                    "— either the year is fresh or the nightly hasn't run. "
                    "Not blocking for silver --full but worth investigating."
                ),
                detail={"per_table": per_table_detail},
            )

        return CheckResult(
            name="corp_actions_year_coverage",
            status="ok",
            message=(
                f"corp_actions covers years "
                f"{start.year}..{today.year} (≥ {_MIN_ROWS_PER_FULL_YEAR:,} "
                f"rows/year; current year {cy}: {cy_rows:,} rows)"
            ),
            detail={"per_table": per_table_detail},
        )
    except Exception as e:
        return CheckResult(
            name="corp_actions_year_coverage",
            status="fail",
            message=f"Unexpected error during year-coverage check: {e}",
            detail={"traceback": traceback.format_exc()},
        )


def check_silver_tables_creatable() -> CheckResult:
    """4: silver.ohlcv_1m + silver.bar_quality can be ensured."""
    try:
        from app.services.iceberg_catalog import get_catalog
        from app.services.silver.tables import (
            ensure_silver_bar_quality,
            ensure_silver_ohlcv_1m,
        )

        cat = get_catalog()
        ohlcv = ensure_silver_ohlcv_1m(cat)
        bq = ensure_silver_bar_quality(cat)
        return CheckResult(
            name="silver_tables_creatable",
            status="ok",
            message="silver.ohlcv_1m + silver.bar_quality ensured",
            detail={
                "ohlcv_identifier": str(ohlcv.name()),
                "bar_quality_identifier": str(bq.name()),
            },
        )
    except Exception as e:
        return CheckResult(
            name="silver_tables_creatable",
            status="fail",
            message=(
                "Failed to ensure silver tables. Check IAM s3:CreateBucket / "
                "glue:CreateTable permissions and ICEBERG_GLUE_DATABASE."
            ),
            detail={"error": f"{type(e).__name__}: {e}"},
        )


def check_end_to_end_slice(symbol: str, day: date) -> CheckResult:
    """5: full pipeline read→normalize→merge→upsert for one (symbol, day)."""
    try:
        from app.services.silver.ohlcv.build import SilverOhlcvBuild

        build = SilverOhlcvBuild.from_settings()
        slice_result = build.build_slice(symbol, day)

        if not slice_result.succeeded:
            return CheckResult(
                name="end_to_end_slice",
                status="fail",
                message=(
                    f"build_slice({symbol}, {day}) raised an error: "
                    f"{slice_result.error}"
                ),
                detail={
                    "symbol": symbol,
                    "date": day.isoformat(),
                    "error": slice_result.error,
                },
            )

        # Bars-read=0 is acceptable IF the day was a weekend / holiday.
        # Otherwise it suggests bronze gaps for the chosen sanity symbol.
        total_read = slice_result.polygon_rows_read + slice_result.schwab_rows_read
        if total_read == 0:
            return CheckResult(
                name="end_to_end_slice",
                status="warn",
                message=(
                    f"Pipeline ran cleanly but bronze had 0 rows for "
                    f"{symbol} × {day}. Try a different --symbol or --day, "
                    f"or check bronze coverage."
                ),
                detail=asdict(slice_result),
            )

        return CheckResult(
            name="end_to_end_slice",
            status="ok",
            message=(
                f"build_slice({symbol}, {day}): "
                f"polygon_read={slice_result.polygon_rows_read} "
                f"schwab_read={slice_result.schwab_rows_read} "
                f"silver_written={slice_result.silver_rows_written} "
                f"quality_written={slice_result.quality_row_written}"
            ),
            detail=asdict(slice_result),
        )
    except Exception as e:
        return CheckResult(
            name="end_to_end_slice",
            status="fail",
            message=f"Unexpected exception during build_slice: {e}",
            detail={"traceback": traceback.format_exc()},
        )


def check_silver_readback(symbol: str, day: date) -> CheckResult:
    """6: read back silver.ohlcv_1m + silver.bar_quality for the slice."""
    try:
        from app.services.readers.silver_ohlcv_reader import SilverOhlcvReader

        reader = SilverOhlcvReader.from_settings()
        start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
        end = start + timedelta(days=1)
        bars_resp = reader.get_bars(symbol, start, end)
        bq_resp = reader.get_bar_quality(symbol, since=day, until=day)

        if bars_resp.count == 0:
            return CheckResult(
                name="silver_readback",
                status="warn",
                message=(
                    f"No silver bars present for {symbol} × {day} "
                    "(likely because the slice ran on a non-trading day). "
                    "This is fine if the previous check warned the same way."
                ),
                detail={
                    "bars_count": 0,
                    "bar_quality_count": bq_resp.count,
                },
            )

        first_ts = bars_resp.bars[0].timestamp.isoformat() if bars_resp.bars else None
        last_ts = bars_resp.bars[-1].timestamp.isoformat() if bars_resp.bars else None
        bq_row = bq_resp.rows[0] if bq_resp.rows else None
        return CheckResult(
            name="silver_readback",
            status="ok",
            message=(
                f"silver.ohlcv_1m has {bars_resp.count} bars for "
                f"{symbol} × {day} [{first_ts}..{last_ts}]; "
                f"bar_quality row present: {bq_row is not None}"
            ),
            detail={
                "bars_count": bars_resp.count,
                "first_ts": first_ts,
                "last_ts": last_ts,
                "bar_quality": (
                    {
                        "expected_bars": bq_row.expected_bars,
                        "actual_bars": bq_row.actual_bars,
                        "gap_count": bq_row.gap_count,
                        "max_gap_minutes": bq_row.max_gap_minutes,
                        "providers_seen": bq_row.providers_seen,
                        "disagreement_count": bq_row.disagreement_count,
                    } if bq_row else None
                ),
                "snapshot_id": bars_resp.snapshot_id,
            },
        )
    except Exception as e:
        return CheckResult(
            name="silver_readback",
            status="fail",
            message=f"Unexpected error reading silver back: {e}",
            detail={"traceback": traceback.format_exc()},
        )


def check_ingestion_runs_recorded() -> CheckResult:
    """7: CH ingestion_runs has at least one silver_ohlcv_build row from
    today. Confirms the audit-log path is wired."""
    try:
        from app.db import get_client

        client = get_client()
        today_iso = datetime.now(timezone.utc).date().isoformat()
        result = client.query(
            """
            SELECT count() AS n, max(finished_at) AS latest
            FROM ingestion_runs
            WHERE job_name = 'silver_ohlcv_build'
              AND toDate(started_at) >= toDate({today:String})
            """,
            parameters={"today": today_iso},
        )
        row = result.result_rows[0]
        n, latest = int(row[0]), row[1]
        if n == 0:
            return CheckResult(
                name="ingestion_runs_recorded",
                status="warn",
                message=(
                    "No silver_ohlcv_build row in CH ingestion_runs for "
                    "today yet. (The preflight slice should have produced "
                    "one — investigate if you ran the slice but the row's "
                    "missing.)"
                ),
                detail={"today": today_iso},
            )
        return CheckResult(
            name="ingestion_runs_recorded",
            status="ok",
            message=f"{n} silver_ohlcv_build run row(s) today; latest finished_at={latest}",
            detail={"count": n, "latest": str(latest)},
        )
    except Exception as e:
        return CheckResult(
            name="ingestion_runs_recorded",
            status="warn",
            message=(
                "Could not query CH ingestion_runs (CH down or table "
                "missing). Silver build will still work — ingestion_runs "
                "is best-effort audit only."
            ),
            detail={"error": f"{type(e).__name__}: {e}"},
        )


# ─────────────────────────────────────────────────────────────────────
# Orchestration
# ─────────────────────────────────────────────────────────────────────


def run_all_checks(symbol: str, day: date) -> list[CheckResult]:
    """Run every check in dependency order. Skips downstream checks if
    catalog is unreachable."""
    checks: list[CheckResult] = []

    cat = check_catalog_reachable()
    checks.append(cat)
    if cat.status == "fail":
        # Skip everything else — every other check needs the catalog.
        for name in (
            "bronze_minute_tables", "silver_corp_actions",
            "corp_actions_year_coverage",
            "silver_tables_creatable", "end_to_end_slice",
            "silver_readback", "ingestion_runs_recorded",
        ):
            checks.append(CheckResult(
                name=name, status="fail",
                message="skipped: catalog unreachable",
            ))
        return checks

    checks.append(check_bronze_minute_tables())
    checks.append(check_silver_corp_actions())
    checks.append(check_corp_actions_year_coverage())
    checks.append(check_silver_tables_creatable())

    # The end-to-end slice depends on bronze having data + silver tables
    # being creatable. Look these up by name (not by index) so adding
    # new checks in the middle doesn't silently break the prereq logic.
    by_name = {c.name: c for c in checks}
    bronze_ok = by_name["bronze_minute_tables"].status in ("ok", "warn")
    silver_ok = by_name["silver_tables_creatable"].status == "ok"
    if bronze_ok and silver_ok:
        checks.append(check_end_to_end_slice(symbol, day))
        # Read back AFTER the slice writes; if the slice failed, skip
        # readback too (it would just confirm the same nothing-written).
        if checks[-1].status in ("ok", "warn"):
            checks.append(check_silver_readback(symbol, day))
        else:
            checks.append(CheckResult(
                name="silver_readback", status="fail",
                message="skipped: end_to_end_slice failed",
            ))
    else:
        for name in ("end_to_end_slice", "silver_readback"):
            checks.append(CheckResult(
                name=name, status="fail",
                message=f"skipped: prereq check failed",
            ))

    checks.append(check_ingestion_runs_recorded())
    return checks


def print_report(checks: list[CheckResult]) -> None:
    print()
    print("─── silver_ohlcv_build PREFLIGHT ───")
    header = f"{'STATUS':<8} {'CHECK':<28} MESSAGE"
    print(header)
    print("-" * len(header))
    for c in checks:
        print(f"{c.glyph:<8} {c.name:<28} {c.message}")
    print()
    # Roll-up.
    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]
    if fails:
        print(
            f"❌ {len(fails)} FAIL — DO NOT run --full until these are fixed."
        )
    elif warns:
        print(
            f"⚠️  {len(warns)} WARN — readable; review before --full but "
            "not blocking."
        )
    else:
        print("✅ ALL OK — safe to run scripts/run_silver_ohlcv_build.py --full")
    print()


def _build_parser() -> argparse.ArgumentParser:
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--symbol", default="AAPL",
        help="Sanity-check symbol (default: AAPL). Pick a high-volume "
             "ticker present in both bronze tables.",
    )
    p.add_argument(
        "--day", type=_parse_date, default=yesterday,
        help=f"Sanity-check day (default: yesterday {yesterday}). Use a "
             "trading day for best results.",
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

    checks = run_all_checks(args.symbol.upper(), args.day)
    print_report(checks)

    if args.out_json:
        payload = {
            "symbol": args.symbol.upper(),
            "day": args.day.isoformat(),
            "started_at": datetime.now(timezone.utc).isoformat(),
            "checks": [asdict(c) for c in checks],
            "summary": {
                "ok": sum(1 for c in checks if c.status == "ok"),
                "warn": sum(1 for c in checks if c.status == "warn"),
                "fail": sum(1 for c in checks if c.status == "fail"),
            },
        }
        args.out_json.write_text(json.dumps(payload, indent=2, default=str))
        print(f"JSON report → {args.out_json}")

    return 0 if all(c.status != "fail" for c in checks) else 2


if __name__ == "__main__":
    sys.exit(main())
