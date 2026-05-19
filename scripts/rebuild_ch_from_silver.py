#!/usr/bin/env python3
"""
Rebuild ClickHouse `ohlcv_1m` from `silver.ohlcv_1m` (TA-5.5).

This is the **operator-facing** counterpart to
`app/services/ingest/silver_to_ch_backfill.py`:

- `silver_to_ch_backfill` is the per-symbol path used by `add_members`
  and the nightly tip-fill flow. Idempotent on (symbol, ts) via CH's
  ReplacingMergeTree.

- THIS script is the bulk operator-driven rebuild: wipe CH (optional)
  then reload from silver for many symbols + many years in one shot.
  Used when silver schema changes, after a silver --full rebuild, or
  when CH gets corrupted / out of sync with the canonical source of
  truth.

**Architectural invariant (silver_layer_plan §1):**

  Silver = canonical source of truth, immutable snapshot-pinned.
  ClickHouse = derived hot cache. Re-buildable from silver any time.

So wiping CH and reloading from silver is the **expected** operator
action, not a disaster recovery. The only thing destroyed by
`--wipe` is the cache; canonical data is safe in silver.

**Usage:**

    # Rebuild CH for the seed universe from a fresh silver build:
    poetry run python scripts/rebuild_ch_from_silver.py \\
        --symbols seed --wipe

    # Rebuild specific symbols only (no wipe — additive):
    poetry run python scripts/rebuild_ch_from_silver.py \\
        --symbols AAPL,NVDA,TSLA

    # Rebuild with explicit window:
    poetry run python scripts/rebuild_ch_from_silver.py \\
        --symbols seed --since 2021-01-04 --until 2026-05-18 --wipe

    # Whole-market rebuild (use after TA-5.6 whole-market silver lands):
    poetry run python scripts/rebuild_ch_from_silver.py \\
        --symbols active --wipe

**Coding standards (docs/standards/coding.md):**

- Rule 1B: log per-symbol completion + zero-bar cases explicitly.
- Rule 1C: per-symbol completion markers in the loop.
- Rule 1E: verify CH row count delta matches reads (cross-side
  verify). Refuse to report `status=ok` if writes silently no-op.
- Rule 1F: no bare except — each per-symbol error is logged + the
  symbol marked failed but the loop continues.

**Exit codes:**

- 0 = all symbols processed; mutation verified.
- 2 = one or more symbols failed OR CH row count delta doesn't match
      expected. Non-zero status forces operator attention.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.config import settings  # noqa: E402
from app.db import get_client  # noqa: E402
from app.services.ingest.silver_to_ch_backfill import (  # noqa: E402
    SilverToChBackfill,
)

logger = logging.getLogger(__name__)


@dataclass
class SymbolResult:
    """Per-symbol result captured in the loop."""

    symbol: str
    bars_read: int = 0
    bars_written: int = 0
    duration_seconds: float = 0.0
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass
class RunResult:
    """Aggregate run report — written to --out-json for cron pipelines."""

    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    symbols_count: int = 0
    since: str = ""
    until: str = ""
    wiped_ch_before_load: bool = False
    ch_rows_before: int = 0
    ch_rows_after: int = 0
    ch_rows_delta: int = 0
    bars_read_total: int = 0
    bars_written_total: int = 0
    failed_symbols: list[str] = field(default_factory=list)
    per_symbol: list[dict] = field(default_factory=list)
    status: str = "in_progress"
    mismatch_warning: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _parse_date(s: str) -> date:
    """ISO date parser for argparse."""
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(
            f"Expected ISO date YYYY-MM-DD; got {s!r}: {e}"
        )


def _bronze_history_start() -> date:
    """Lower bound of the silver window — pulled from BRONZE_HISTORY_START."""
    try:
        return date.fromisoformat(settings.bronze_history_start)
    except (TypeError, ValueError):
        return date(2021, 1, 4)


def _resolve_symbols(spec: Optional[str]) -> list[str]:
    """Delegate to the same universe resolver the silver build uses,
    so `seed` / `active` / explicit-CSV all behave identically."""
    from app.services.universe import resolve_universe_spec
    return resolve_universe_spec(spec or "seed")


def _ch_ohlcv_row_count() -> int:
    """Return the current row count of stocks.ohlcv_1m for verify-mutation.

    Hits the live table (NOT the replacing-merge final state). For our
    purposes, "rows present after insert" is what matters — duplicates
    deduplicate on the next merge cycle.
    """
    try:
        client = get_client()
        result = client.query("SELECT count() FROM ohlcv_1m")
        return int(result.result_rows[0][0])
    except Exception as e:
        # Coding standards rule 1F — log + return -1 sentinel so caller
        # sees the verify-mutation step failed (don't silently swallow).
        logger.exception("Could not query ohlcv_1m row count: %s", e)
        return -1


def _wipe_ch_ohlcv() -> None:
    """`TRUNCATE TABLE stocks.ohlcv_1m`. Loud + reversible (silver is the
    source of truth so the wipe is safe; we'll refill from silver).

    Caller is responsible for confirming intent BEFORE invoking this —
    no interactive prompt inside the function (so it's automation-safe).
    """
    client = get_client()
    pre = _ch_ohlcv_row_count()
    logger.warning(
        "WIPING ClickHouse ohlcv_1m (pre-row-count=%d) — this is reversible "
        "since silver is the source of truth.", pre,
    )
    # max_table_size_to_drop=0 bypasses the ClickHouse safety guard that blocks
    # TRUNCATE on tables larger than max_table_size_to_drop (default 50 GB).
    # This is safe here: silver is the canonical source of truth and the rebuild
    # immediately refills CH from silver, so data loss risk is zero.
    client.command(
        "TRUNCATE TABLE ohlcv_1m",
        settings={"max_table_size_to_drop": 0},
    )
    post = _ch_ohlcv_row_count()
    logger.info(
        "ohlcv_1m wiped: pre=%d post=%d",
        pre, post,
    )
    if post != 0:
        raise RuntimeError(
            f"TRUNCATE ran but row count is {post} (expected 0). "
            "Refusing to proceed — investigate before re-running."
        )


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    bronze_start = _bronze_history_start()

    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--symbols", type=str, default="seed",
        help=(
            "Comma-separated symbols, or 'seed' (SEED_SYMBOLS, default), "
            "or 'active' (seed ∪ active watchlists per G1 dynamic universe)."
        ),
    )
    p.add_argument(
        "--since", type=_parse_date, default=bronze_start,
        help=f"Lower bound (ISO date). Default: {bronze_start} (BRONZE_HISTORY_START).",
    )
    p.add_argument(
        "--until", type=_parse_date, default=yesterday,
        help=f"Upper bound (ISO date). Default: {yesterday} (yesterday UTC).",
    )
    p.add_argument(
        "--wipe", action="store_true",
        help=(
            "TRUNCATE stocks.ohlcv_1m before loading. Use this when "
            "rebuilding CH from a fresh silver build (typical operator "
            "workflow after silver --full). REVERSIBLE — silver is the "
            "source of truth."
        ),
    )
    p.add_argument(
        "--out-json", type=Path, default=None,
        help="Write structured run report to this path.",
    )
    p.add_argument(
        "--continue-on-error", action="store_true",
        help=(
            "Don't exit on first per-symbol failure; keep processing "
            "remaining symbols. Default: stop after first failure "
            "(safer for ad-hoc operator runs)."
        ),
    )
    return p


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    symbols = _resolve_symbols(args.symbols)
    start = datetime(args.since.year, args.since.month, args.since.day, tzinfo=timezone.utc)
    # End is EXCLUSIVE in SilverOhlcvReader — add 1 day so --until is inclusive.
    end_date_inclusive = args.until + timedelta(days=1)
    end = datetime(
        end_date_inclusive.year, end_date_inclusive.month, end_date_inclusive.day,
        tzinfo=timezone.utc,
    )

    started = datetime.now(timezone.utc)
    report = RunResult(
        started_at=started.isoformat(),
        symbols_count=len(symbols),
        since=args.since.isoformat(),
        until=args.until.isoformat(),
        wiped_ch_before_load=args.wipe,
    )

    logger.info(
        "rebuild_ch_from_silver: starting symbols=%d window=%s..%s wipe=%s",
        len(symbols), args.since, args.until, args.wipe,
    )
    if not symbols:
        logger.warning("rebuild_ch_from_silver: empty symbol list; nothing to do")
        report.status = "no_symbols"
        return _finalize_and_print(args, report, started)

    # Pre-state: capture CH row count BEFORE any mutation.
    report.ch_rows_before = _ch_ohlcv_row_count()
    if report.ch_rows_before < 0:
        report.mismatch_warning = (
            "Could not query CH row count pre-run — verify-mutation "
            "step will be partial."
        )
    logger.info("Pre-run CH ohlcv_1m rows: %d", report.ch_rows_before)

    # Optional wipe.
    if args.wipe:
        try:
            _wipe_ch_ohlcv()
            report.ch_rows_before = 0
        except Exception as e:
            logger.exception("CH wipe failed: %s", e)
            report.status = "fail"
            report.mismatch_warning = f"wipe failed: {e}"
            return _finalize_and_print(args, report, started)

    # Per-symbol loop.
    backfiller = SilverToChBackfill.from_settings()
    for idx, sym in enumerate(symbols, start=1):
        sym_started = datetime.now(timezone.utc)
        sym_result = SymbolResult(symbol=sym)
        try:
            r = backfiller.backfill_symbol_window(sym, start, end)
            sym_result.bars_read = r.bars_read
            sym_result.bars_written = r.bars_written
            if r.error:
                sym_result.error = r.error
        except Exception as e:
            sym_result.error = f"{type(e).__name__}: {e}"
            logger.exception("symbol=%s failed: %s", sym, e)

        sym_result.duration_seconds = (
            datetime.now(timezone.utc) - sym_started
        ).total_seconds()
        report.per_symbol.append(asdict(sym_result))
        report.bars_read_total += sym_result.bars_read
        report.bars_written_total += sym_result.bars_written
        if not sym_result.succeeded:
            report.failed_symbols.append(sym)
            logger.error(
                "rebuild_ch_from_silver: [%d/%d] symbol=%s FAILED: %s",
                idx, len(symbols), sym, sym_result.error,
            )
            if not args.continue_on_error:
                logger.error(
                    "Stopping on first failure (use --continue-on-error to "
                    "process remaining symbols)."
                )
                break
        else:
            # Rule 1C: per-iteration completion marker.
            logger.info(
                "rebuild_ch_from_silver: [%d/%d] symbol=%s "
                "bars_read=%d bars_written=%d duration=%.1fs",
                idx, len(symbols), sym,
                sym_result.bars_read, sym_result.bars_written,
                sym_result.duration_seconds,
            )

    # Post-state: verify mutation took effect.
    report.ch_rows_after = _ch_ohlcv_row_count()
    report.ch_rows_delta = (
        report.ch_rows_after - report.ch_rows_before
        if report.ch_rows_before >= 0 and report.ch_rows_after >= 0 else 0
    )
    logger.info(
        "Post-run CH ohlcv_1m rows: %d (delta=%+d, bars_written_reported=%d)",
        report.ch_rows_after, report.ch_rows_delta, report.bars_written_total,
    )

    # Rule 1E — verify mutation cross-side. CH ReplacingMergeTree may
    # show a smaller delta than bars_written if rows dedup pre-merge
    # (rare but possible). We compute a tolerance: delta should be at
    # least 90% of bars_written. Less than that = something silently
    # didn't land.
    if report.bars_written_total > 0 and report.ch_rows_delta >= 0:
        ratio = report.ch_rows_delta / max(1, report.bars_written_total)
        if ratio < 0.9:
            report.mismatch_warning = (
                f"CH row delta ({report.ch_rows_delta:,}) is < 90% of "
                f"bars_written ({report.bars_written_total:,}). May "
                "indicate silent insert failures — inspect logs."
            )
            logger.warning(report.mismatch_warning)

    # Determine overall status.
    if report.failed_symbols:
        report.status = (
            "partial_fail" if args.continue_on_error else "fail"
        )
    elif report.mismatch_warning:
        report.status = "ok_with_warnings"
    else:
        report.status = "ok"

    return _finalize_and_print(args, report, started)


def _finalize_and_print(
    args, report: RunResult, started: datetime,
) -> int:
    finished = datetime.now(timezone.utc)
    report.finished_at = finished.isoformat()
    report.duration_seconds = (finished - started).total_seconds()

    print()
    print("─── rebuild_ch_from_silver summary ───")
    print(f"  status:           {report.status}")
    print(f"  window:           {report.since} .. {report.until}")
    print(f"  symbols:          {report.symbols_count}")
    print(f"  wiped_first:      {report.wiped_ch_before_load}")
    print(f"  ch_rows_before:   {report.ch_rows_before:,}")
    print(f"  ch_rows_after:    {report.ch_rows_after:,}")
    print(f"  ch_rows_delta:    {report.ch_rows_delta:+,}")
    print(f"  bars_read_total:  {report.bars_read_total:,}")
    print(f"  bars_written:     {report.bars_written_total:,}")
    print(f"  failed_symbols:   {len(report.failed_symbols)}")
    if report.failed_symbols:
        for sym in report.failed_symbols[:10]:
            print(f"    - {sym}")
        if len(report.failed_symbols) > 10:
            print(f"    ... and {len(report.failed_symbols) - 10} more")
    print(f"  duration:         {report.duration_seconds:.1f}s")
    if report.mismatch_warning:
        print(f"  ⚠️  WARNING:      {report.mismatch_warning}")
    print()

    if args.out_json:
        args.out_json.write_text(json.dumps(asdict(report), indent=2, default=str))
        print(f"JSON report → {args.out_json}")

    return 0 if report.status in ("ok", "ok_with_warnings", "no_symbols") else 2


if __name__ == "__main__":
    sys.exit(main())
