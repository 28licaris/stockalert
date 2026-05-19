#!/usr/bin/env python3
"""
Run Polygon corp-actions backfill — bronze + silver in one shot.

Two phases per invocation:

1. **Bronze ingest** (Polygon REST → `bronze.polygon_corp_actions`):
   Pulls splits + dividends in the requested window via
   `PolygonCorpActionsBronzeIngest`. Idempotent via Iceberg upsert
   on (symbol, ex_date, action_type).

2. **Silver build** (`bronze.{provider}_corp_actions` →
   `silver.corp_actions`): Merges all configured bronze provider
   tables with precedence, upserts into silver. Same upsert
   idempotency.

**Modes:**

    # One-shot historical backfill (default 2003-01-01 → yesterday):
    poetry run python scripts/run_corp_actions_backfill.py --full

    # Yesterday only (incremental; suitable for nightly cron):
    poetry run python scripts/run_corp_actions_backfill.py --nightly

    # Custom window:
    poetry run python scripts/run_corp_actions_backfill.py \\
        --since 2020-01-01 --until 2020-12-31

    # Skip silver build (bronze only — for parallel multi-provider ingest):
    poetry run python scripts/run_corp_actions_backfill.py --nightly --bronze-only

    # Skip bronze ingest (silver-only — useful after manual bronze fix):
    poetry run python scripts/run_corp_actions_backfill.py --full --silver-only

Exits non-zero on any phase failure. Prints a structured summary +
optional JSON report for cron pipelines.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.services.silver.corp_actions import (  # noqa: E402
    PolygonCorpActionsBronzeIngest,
    SilverCorpActionsBuild,
)

logger = logging.getLogger(__name__)


def _parse_date(s: str) -> date:
    """ISO date parser for argparse."""
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"Expected ISO date YYYY-MM-DD; got {s!r}: {e}"
        )


async def run_bronze(since: date, until: date) -> dict:
    """Stage 1: Polygon REST → bronze.polygon_corp_actions.

    **Verify-mutation contract (coding_standards.md rule 3):**
    After the upsert path completes, we reload the bronze table via
    a fresh catalog instance and assert the snapshot ID changed
    (or, if the input chunk had zero rows after dedupe, the row count
    matches what we tried to write). Without this assertion a
    silently-killed python process can "succeed" without writing.
    """
    from app.services.bronze.schemas import bronze_table_id
    from app.services.iceberg_catalog import get_catalog

    logger.info("=== Stage 1/2: bronze ingest (Polygon REST → bronze) ===")

    # Pre-state — captured BEFORE the ingest so post-verify is a true delta.
    pre_cat = get_catalog()
    pre_tbl = pre_cat.load_table(bronze_table_id("polygon_corp_actions"))
    pre_snap = pre_tbl.current_snapshot()
    pre_snap_id = str(pre_snap.snapshot_id) if pre_snap else None
    pre_rows = (
        int(pre_snap.summary.additional_properties.get("total-records", 0))
        if pre_snap else 0
    )
    logger.info(
        "Pre-ingest bronze state: snapshot_id=%s total_rows=%d",
        pre_snap_id, pre_rows,
    )

    ingest = PolygonCorpActionsBronzeIngest.from_settings()
    result = await ingest.backfill_full_history(since=since, until=until)
    logger.info(
        "Bronze ingest done: splits=%d dividends=%d duration=%.1fs",
        result["splits_written"],
        result["dividends_written"],
        result["duration_seconds"],
    )

    # Post-state — fresh catalog instance to bypass any caching.
    expected_writes = result["splits_written"] + result["dividends_written"]
    post_cat = get_catalog()
    post_tbl = post_cat.load_table(bronze_table_id("polygon_corp_actions"))
    post_snap = post_tbl.current_snapshot()
    post_snap_id = str(post_snap.snapshot_id) if post_snap else None
    post_rows = (
        int(post_snap.summary.additional_properties.get("total-records", 0))
        if post_snap else 0
    )
    logger.info(
        "Post-ingest bronze state: snapshot_id=%s total_rows=%d delta=%+d",
        post_snap_id, post_rows, post_rows - pre_rows,
    )

    if expected_writes > 0 and post_snap_id == pre_snap_id:
        # We tried to write but the snapshot ID didn't change. This is
        # the silent-failure signature (process killed mid-commit, etc).
        raise RuntimeError(
            f"bronze upsert NO-OP detected: ingest claimed "
            f"{expected_writes:,} rows written but snapshot_id is "
            f"unchanged ({pre_snap_id}). Table was NOT modified. "
            "Likely cause: process killed mid-upsert (OOM / SIGKILL) "
            "or a swallowed exception. Re-run after investigating."
        )

    result["pre_snapshot_id"] = pre_snap_id
    result["post_snapshot_id"] = post_snap_id
    result["pre_rows"] = pre_rows
    result["post_rows"] = post_rows
    result["row_delta"] = post_rows - pre_rows
    return result


def run_silver(since: Optional[date]) -> dict:
    """Stage 2: bronze.{provider}_corp_actions → silver.corp_actions.

    Pass since=None to merge the full bronze history (run_full);
    pass a date for incremental (run_since).
    """
    logger.info("=== Stage 2/2: silver build (bronze → silver, precedence merge) ===")
    build = SilverCorpActionsBuild.from_settings()
    if since is None:
        result = build.run_full()
    else:
        result = build.run_since(since)
    logger.info(
        "Silver build done: providers_read=%s rows_merged=%s "
        "rows_updated=%s rows_inserted=%s duration=%.1fs",
        result.get("providers_read"),
        result.get("rows_merged"),
        result.get("rows_updated"),
        result.get("rows_inserted"),
        result.get("duration_seconds"),
    )
    return result


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    mode = p.add_mutually_exclusive_group()
    mode.add_argument(
        "--full",
        action="store_true",
        help=(
            "Full historical backfill since 2003-01-01 (use once when "
            "seeding the lake)."
        ),
    )
    mode.add_argument(
        "--nightly",
        action="store_true",
        help=(
            "Yesterday-only incremental (use as nightly cron). "
            "Idempotent — safe to re-run."
        ),
    )
    # Custom window — overrides modes above.
    p.add_argument(
        "--since",
        type=_parse_date,
        default=None,
        help="Custom lower bound (ISO date). Overrides --full/--nightly.",
    )
    p.add_argument(
        "--until",
        type=_parse_date,
        default=None,
        help="Custom upper bound (ISO date). Defaults to yesterday.",
    )
    # Phase toggles
    p.add_argument(
        "--bronze-only",
        action="store_true",
        help="Skip stage 2 (silver build). Useful for parallel multi-provider ingest.",
    )
    p.add_argument(
        "--silver-only",
        action="store_true",
        help=(
            "Skip stage 1 (bronze ingest). Run silver merge against whatever's "
            "already in bronze. Useful after manual bronze corrections."
        ),
    )
    p.add_argument(
        "--out-json",
        type=Path,
        default=None,
        help="Write structured run report to this path.",
    )
    return p


def _resolve_window(args) -> tuple[date, date]:
    """Translate flags + dates into (since, until)."""
    yesterday = (datetime.now(timezone.utc).date() - timedelta(days=1))

    if args.since is not None or args.until is not None:
        since = args.since or date(2003, 1, 1)
        until = args.until or yesterday
        return since, until

    if args.full:
        return date(2003, 1, 1), yesterday

    # default: nightly
    return yesterday, yesterday


async def main() -> int:
    args = _build_parser().parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.bronze_only and args.silver_only:
        print("FAIL: --bronze-only and --silver-only are mutually exclusive.",
              file=sys.stderr)
        return 2

    since, until = _resolve_window(args)
    logger.info(
        "Corp-actions backfill: since=%s until=%s bronze_only=%s silver_only=%s",
        since, until, args.bronze_only, args.silver_only,
    )

    summary: dict = {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "bronze_only": args.bronze_only,
        "silver_only": args.silver_only,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "bronze": None,
        "silver": None,
        "status": "in_progress",
    }

    try:
        if not args.silver_only:
            summary["bronze"] = await run_bronze(since, until)

        if not args.bronze_only:
            # Silver build uses the same `since` so it only re-merges the
            # newly-touched window (incremental); full backfill uses
            # since=date(2003,1,1) which is effectively "merge all".
            silver_since = None if (args.full and not args.since) else since
            summary["silver"] = run_silver(silver_since)

        summary["status"] = "ok"
    except Exception as e:
        summary["status"] = "fail"
        summary["error"] = f"{type(e).__name__}: {e}"
        logger.exception("corp-actions backfill failed")

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()

    print()
    print("─── corp_actions_backfill summary ───")
    print(f"  status:       {summary['status']}")
    print(f"  window:       {summary['since']} .. {summary['until']}")
    if summary["bronze"]:
        b = summary["bronze"]
        print(
            f"  bronze:       splits={b['splits_written']}  "
            f"dividends={b['dividends_written']}  "
            f"duration={b['duration_seconds']:.1f}s"
        )
    if summary["silver"]:
        s = summary["silver"]
        print(
            f"  silver:       rows_merged={s.get('rows_merged')}  "
            f"inserted={s.get('rows_inserted')}  "
            f"updated={s.get('rows_updated')}  "
            f"duration={s.get('duration_seconds', 0):.1f}s"
        )
    if "error" in summary:
        print(f"  error:        {summary['error']}")
    print()

    if args.out_json:
        args.out_json.write_text(json.dumps(summary, indent=2, default=str))
        print(f"JSON report → {args.out_json}")

    return 0 if summary["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
