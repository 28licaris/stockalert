#!/usr/bin/env python3
"""
Run silver OHLCV build — bronze.{provider}_minute → silver.ohlcv_1m + bar_quality.

Three modes:

  # Yesterday only for the seed universe (matches the nightly loop):
  poetry run python scripts/run_silver_ohlcv_build.py --nightly

  # Initial full backfill: every day from start of bronze coverage to yesterday:
  poetry run python scripts/run_silver_ohlcv_build.py --full

  # Custom window:
  poetry run python scripts/run_silver_ohlcv_build.py \
      --since 2024-06-01 --until 2024-06-30

Symbol selection:
  --symbols <spec>:
      "seed"          → SEED_SYMBOLS (default)
      "AAPL,NVDA"     → explicit list
      (omitted)       → SEED_SYMBOLS

Output: a structured summary printed to stdout. With ``--out-json`` the
same summary is written to disk (useful for pipelines that consume the
result).

Idempotent: re-running is safe — PyIceberg upserts on the silver
identifiers (`(symbol, ts)` for ohlcv_1m, `(symbol, date)` for
bar_quality) handle re-writes cleanly.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.data.seed_universe import SEED_SYMBOLS  # noqa: E402
from app.services.silver.ohlcv.build import (  # noqa: E402
    BuildResult,
    SilverOhlcvBuild,
)

logger = logging.getLogger(__name__)


def _parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"Expected ISO date YYYY-MM-DD; got {s!r}: {e}"
        )


def _resolve_symbols(spec: Optional[str]) -> list[str]:
    s = (spec or "").strip().lower()
    if not s or s in ("seed", "seed-100", "seed_100"):
        return list(SEED_SYMBOLS)
    return [tok.strip().upper() for tok in spec.split(",") if tok.strip()]


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
            "Full historical backfill (default since=2021-01-04, "
            "until=yesterday). Use once when seeding silver from bronze."
        ),
    )
    mode.add_argument(
        "--nightly",
        action="store_true",
        help=(
            "Yesterday-only build. Matches what the in-process nightly "
            "loop runs. Idempotent."
        ),
    )
    mode.add_argument(
        "--rebuild-corp-action-dirty",
        action="store_true",
        help=(
            "Scan silver.corp_actions for splits ingested since the last "
            "successful silver_ohlcv_build run and rebuild every affected "
            "symbol's full history before each new ex_date. Manual operator "
            "trigger for the same corp-action-rebuild logic the nightly "
            "loop runs automatically (TA-5.1.9)."
        ),
    )
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
    p.add_argument(
        "--symbols",
        type=str,
        default=None,
        help=(
            "Comma-separated symbols, or 'seed' for SEED_SYMBOLS "
            "(default). Examples: 'seed', 'AAPL,NVDA,MSFT'."
        ),
    )
    p.add_argument(
        "--mode",
        type=str,
        choices=["month", "per-slice"],
        default="month",
        help=(
            "Bronze scan strategy (TA-5.1.11). Default 'month': ONE "
            "scan per provider per month for ~2000× fewer S3 round-"
            "trips. 'per-slice' falls back to the legacy per-(symbol, "
            "day) scan path — mostly useful for debugging or single-"
            "slice rebuilds. Output is byte-identical either way."
        ),
    )
    p.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help=(
            "Per-slice mode only (TA-5.1.10): number of slices to "
            "compute in parallel. Default 1 (sequential). Ignored "
            "when --mode=month (already fast enough without)."
        ),
    )
    p.add_argument(
        "--out-json",
        type=Path,
        default=None,
        help="Write structured run summary to this path.",
    )
    return p


def _resolve_window(args) -> tuple[date, date]:
    """Translate flags into (since, until)."""
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)

    if args.since is not None or args.until is not None:
        since = args.since or date(2021, 1, 4)
        until = args.until or yesterday
        return since, until

    if args.full:
        return date(2021, 1, 4), yesterday

    # default: nightly
    return yesterday, yesterday


def _summarize(result: BuildResult) -> dict:
    return {
        "run_id": result.run_id,
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
        "duration_seconds": result.duration_seconds,
        "symbols": len(result.symbols),
        "start_date": result.start_date.isoformat() if result.start_date else None,
        "end_date": result.end_date.isoformat() if result.end_date else None,
        "slices": len(result.slices),
        "slices_succeeded": result.slices_succeeded,
        "slices_failed": result.slices_failed,
        "silver_rows": result.total_silver_rows,
        # Carry per-slice errors when there are any so operators can
        # debug without a separate query.
        "errors": [
            {
                "symbol": s.symbol,
                "date": s.date.isoformat(),
                "error": s.error,
            }
            for s in result.slices if not s.succeeded
        ][:50],  # cap at 50 — operators get the gist; full list is in logs
    }


def _run_corp_action_dirty(args) -> int:
    """Manual operator trigger for the corp-action rebuild logic (TA-5.1.9).

    Same scan + rebuild the nightly loop runs automatically. Use this
    when you've just landed a corp_actions ingest manually and want
    historical silver slices recomputed immediately rather than waiting
    for the next nightly.
    """
    summary: dict = {
        "mode": "rebuild_corp_action_dirty",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "in_progress",
    }

    try:
        build = SilverOhlcvBuild.from_settings()
        result = build._run_corp_action_dirty_rebuilds()
        if result is None:
            summary["status"] = "no_dirty_symbols"
            summary["result"] = {"slices": 0, "symbols": 0}
        else:
            summary["result"] = _summarize(result)
            summary["status"] = (
                "ok" if result.slices_failed == 0 else "partial_fail"
            )
    except Exception as e:
        summary["status"] = "fail"
        summary["error"] = f"{type(e).__name__}: {e}"
        logger.exception("rebuild_corp_action_dirty failed")

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()

    print()
    print("─── silver_ohlcv_build (corp-action dirty rebuild) ───")
    print(f"  status:       {summary['status']}")
    if summary["status"] == "no_dirty_symbols":
        print(
            "  no symbols flagged dirty — no new corp_actions since the "
            "last successful silver_ohlcv_build run"
        )
    elif "result" in summary:
        r = summary["result"]
        print(
            f"  symbols:      {r['symbols']}  "
            f"slices:       {r['slices']}  "
            f"(ok={r['slices_succeeded']} fail={r['slices_failed']})"
        )
        print(f"  silver_rows:  {r['silver_rows']}")
        print(f"  duration:     {r['duration_seconds']:.1f}s")
    if "error" in summary:
        print(f"  error:        {summary['error']}")
    print()

    if args.out_json:
        args.out_json.write_text(json.dumps(summary, indent=2, default=str))
        print(f"JSON report → {args.out_json}")

    return 0 if summary["status"] in ("ok", "no_dirty_symbols") else 2


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    # Dirty-rebuild mode is a different path — bypass window resolution.
    if args.rebuild_corp_action_dirty:
        return _run_corp_action_dirty(args)

    since, until = _resolve_window(args)
    symbols = _resolve_symbols(args.symbols)
    concurrency = max(1, int(args.concurrency))
    logger.info(
        "silver_ohlcv_build: window=%s..%s symbols=%d (full=%s nightly=%s) "
        "concurrency=%d",
        since, until, len(symbols), args.full, args.nightly, concurrency,
    )

    summary: dict = {
        "since": since.isoformat(),
        "until": until.isoformat(),
        "symbols_count": len(symbols),
        "concurrency": concurrency,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "status": "in_progress",
    }

    try:
        build = SilverOhlcvBuild.from_settings()
        result = build.build_window(
            symbols, since, until,
            mode=args.mode,
            max_concurrency=concurrency,
        )
        summary["mode"] = args.mode
        summary["result"] = _summarize(result)
        summary["status"] = "ok" if result.slices_failed == 0 else "partial_fail"
    except Exception as e:
        summary["status"] = "fail"
        summary["error"] = f"{type(e).__name__}: {e}"
        logger.exception("silver_ohlcv_build failed")

    summary["finished_at"] = datetime.now(timezone.utc).isoformat()

    # Pretty stdout summary.
    print()
    print("─── silver_ohlcv_build summary ───")
    print(f"  status:       {summary['status']}")
    print(f"  window:       {summary['since']} .. {summary['until']}")
    print(f"  symbols:      {summary['symbols_count']}")
    if "result" in summary:
        r = summary["result"]
        print(
            f"  slices:       {r['slices']}  "
            f"(ok={r['slices_succeeded']} fail={r['slices_failed']})"
        )
        print(f"  silver_rows:  {r['silver_rows']}")
        print(f"  duration:     {r['duration_seconds']:.1f}s")
        if r["slices_failed"]:
            print(f"  first errors: {r['errors'][:3]}")
    if "error" in summary:
        print(f"  error:        {summary['error']}")
    print()

    if args.out_json:
        args.out_json.write_text(json.dumps(summary, indent=2, default=str))
        print(f"JSON report → {args.out_json}")

    return 0 if summary["status"] == "ok" else 2


if __name__ == "__main__":
    sys.exit(main())
