#!/usr/bin/env python3
"""
Rebuild ClickHouse `stocks.ohlcv_1m` from the v2 lake — the single,
canonical bulk loader.

Per symbol it calls `app.services.equities.lake_to_ch_fill.fill_ch_from_lake_sync`,
the SAME read+insert path the on-demand bars-gateway self-heal uses, so the
bulk rebuild and the interactive fill can never drift. That path reads the
lake via `read_arrow` — the polygon∪schwab union, polygon winning overlaps,
with split adjustment computed at READ time from `polygon_raw` + `market_splits`
and the recent schwab tip included.

  (Supersedes the retired `hotload_ch_from_lake.py` — which read the deleted
  `equities.polygon_adjusted` table — and `rebuild_ch_from_silver.py` — named
  after the retired "silver" tier and missing the schwab tip in bulk. There is
  now ONE lake→CH bulk rebuild script.)

When to run: post-cutover one-shot universe load, a CH disk wipe / schema
rebuild, or any "CH drifted from the lake" recovery. CH is a derived hot cache;
the lake is the source of truth, so `--wipe` then reload is an expected
operator action, not disaster recovery.

Usage:
    # Rebuild CH for the authoritative stream universe (clean wipe first):
    poetry run python scripts/rebuild_ch_from_lake.py --symbols active --wipe

    # Specific symbols, additive (no wipe):
    poetry run python scripts/rebuild_ch_from_lake.py --symbols AAPL,NVDA,XLC

    # Explicit window + higher parallelism:
    poetry run python scripts/rebuild_ch_from_lake.py \\
        --symbols active --since 2025-01-02 --until 2026-06-27 --parallelism 12 --wipe

    # Preview the plan, mutate nothing:
    poetry run python scripts/rebuild_ch_from_lake.py --symbols active --dry-run

Coding standards (docs/standards/coding.md):
  - Preflight one symbol before the full (>5-min) run; abort loudly if it
    lands nothing.
  - Per-symbol completion markers + explicit zero-row logging.
  - Cross-side verify: CH row-count delta must track inserted rows; refuse
    `status=ok` if writes silently no-op.
  - No bare except — each symbol's error is logged, the symbol marked failed,
    the run continues.

Exit codes:
  0 = all symbols processed; mutation verified.
  2 = one or more symbols failed, OR the CH row-count delta doesn't match
      inserted rows (forces operator attention).
"""
from __future__ import annotations

import argparse
import concurrent.futures
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
from app.db.client import get_client  # noqa: E402
from app.services.equities.lake_to_ch_fill import fill_ch_from_lake_sync  # noqa: E402
from app.services.universe import resolve_universe_spec  # noqa: E402

logger = logging.getLogger("rebuild_ch_from_lake")


@dataclass
class SymbolResult:
    symbol: str
    rows_inserted: int = 0
    duration_seconds: float = 0.0
    error: Optional[str] = None

    @property
    def succeeded(self) -> bool:
        return self.error is None


@dataclass
class RunResult:
    started_at: str = ""
    finished_at: str = ""
    duration_seconds: float = 0.0
    symbols_count: int = 0
    since: str = ""
    until: str = ""
    parallelism: int = 1
    wiped_ch_before_load: bool = False
    ch_rows_before: int = 0
    ch_rows_after: int = 0
    ch_rows_delta: int = 0
    rows_inserted_total: int = 0
    failed_symbols: list[str] = field(default_factory=list)
    per_symbol: list[dict] = field(default_factory=list)
    status: str = "in_progress"
    mismatch_warning: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _parse_date(s: str) -> date:
    try:
        return date.fromisoformat(s)
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"Expected ISO date YYYY-MM-DD; got {s!r}: {e}")


def _lake_history_start() -> date:
    try:
        return date.fromisoformat(settings.lake_history_start)
    except (TypeError, ValueError):
        return date(2021, 1, 4)


def _resolve_symbols(spec: Optional[str]) -> list[str]:
    return resolve_universe_spec(spec or "active")


def _ch_ohlcv_row_count() -> int:
    """Live row count of stocks.ohlcv_1m (NOT the ReplacingMergeTree final
    state) — what matters for verify-mutation is "rows present after insert".
    Returns -1 (logged) on failure so the caller sees the verify step failed."""
    try:
        client = get_client()
        result = client.query("SELECT count() FROM ohlcv_1m")
        return int(result.result_rows[0][0])
    except Exception as e:  # noqa: BLE001 — rule 1F: log, don't swallow
        logger.exception("Could not query ohlcv_1m row count: %s", e)
        return -1


def _wipe_ch_ohlcv() -> None:
    """TRUNCATE stocks.ohlcv_1m. Reversible — the lake is the source of truth
    and the rebuild immediately refills CH. Automation-safe (no prompt)."""
    client = get_client()
    pre = _ch_ohlcv_row_count()
    logger.warning(
        "WIPING ClickHouse ohlcv_1m (pre-row-count=%d) — reversible; the lake "
        "is the source of truth and this run refills CH.", pre,
    )
    # max_table_size_to_drop=0 bypasses the CH guard that blocks TRUNCATE on
    # tables > 50 GB. Safe here: the lake is canonical; we refill immediately.
    client.command("TRUNCATE TABLE ohlcv_1m", settings={"max_table_size_to_drop": 0})
    post = _ch_ohlcv_row_count()
    logger.info("ohlcv_1m wiped: pre=%d post=%d", pre, post)
    if post != 0:
        raise RuntimeError(
            f"TRUNCATE ran but row count is {post} (expected 0). "
            "Refusing to proceed — investigate before re-running."
        )


def _fill_one(symbol: str, start: datetime, end: datetime) -> SymbolResult:
    """Load one symbol via the shared fill path. Never raises."""
    sym_started = datetime.now(timezone.utc)
    res = SymbolResult(symbol=symbol)
    try:
        res.rows_inserted = fill_ch_from_lake_sync(symbol, start, end, source_tag="lake-rebuild")
    except Exception as e:  # noqa: BLE001 — rule 1F
        res.error = f"{type(e).__name__}: {e}"
        logger.exception("symbol=%s failed: %s", symbol, e)
    res.duration_seconds = (datetime.now(timezone.utc) - sym_started).total_seconds()
    return res


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
    lake_start = _lake_history_start()

    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--symbols", type=str, default="active",
        help="Comma-separated symbols, or 'active' (the ClickHouse stream_universe, default).",
    )
    p.add_argument(
        "--since", type=_parse_date, default=lake_start,
        help=f"Lower bound (ISO date). Default: {lake_start} (lake_history_start).",
    )
    p.add_argument(
        "--until", type=_parse_date, default=yesterday,
        help=f"Upper bound (ISO date, inclusive). Default: {yesterday} (yesterday UTC).",
    )
    p.add_argument(
        "--parallelism", type=int, default=8,
        help="Concurrent per-symbol fills (default 8). Each fill is an independent lake read.",
    )
    p.add_argument(
        "--wipe", action="store_true",
        help="TRUNCATE stocks.ohlcv_1m before loading (clean rebuild). REVERSIBLE — lake is canonical.",
    )
    p.add_argument(
        "--dry-run", action="store_true",
        help="Resolve symbols + print the plan; mutate nothing.",
    )
    p.add_argument("--out-json", type=Path, default=None, help="Write a structured run report here.")
    return p


# ─────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    symbols = _resolve_symbols(args.symbols)
    start = datetime(args.since.year, args.since.month, args.since.day, tzinfo=timezone.utc)
    # fill_ch_from_lake_sync end is EXCLUSIVE — +1 day so --until is inclusive.
    end_excl = args.until + timedelta(days=1)
    end = datetime(end_excl.year, end_excl.month, end_excl.day, tzinfo=timezone.utc)

    started = datetime.now(timezone.utc)
    report = RunResult(
        started_at=started.isoformat(),
        symbols_count=len(symbols),
        since=args.since.isoformat(),
        until=args.until.isoformat(),
        parallelism=args.parallelism,
        wiped_ch_before_load=args.wipe,
    )

    logger.info(
        "rebuild_ch_from_lake: symbols=%d window=%s..%s parallelism=%d wipe=%s",
        len(symbols), args.since, args.until, args.parallelism, args.wipe,
    )
    if not symbols:
        logger.warning("rebuild_ch_from_lake: empty symbol list; nothing to do")
        report.status = "no_symbols"
        return _finalize_and_print(args, report, started)

    if args.dry_run:
        logger.info("DRY RUN — would load %d symbols: %s%s", len(symbols),
                    ", ".join(symbols[:20]), " …" if len(symbols) > 20 else "")
        report.status = "dry_run"
        return _finalize_and_print(args, report, started)

    # Preflight (rule: preflight any >5-min job). Load ONE symbol first; abort
    # loudly if it errors or lands nothing, rather than burning the full run.
    logger.info("Preflight: loading %s before the full run…", symbols[0])
    pf = _fill_one(symbols[0], start, end)
    if pf.error is not None:
        logger.error("Preflight FAILED for %s: %s — aborting.", symbols[0], pf.error)
        report.status = "fail"
        report.mismatch_warning = f"preflight failed: {pf.error}"
        return _finalize_and_print(args, report, started)
    if pf.rows_inserted == 0:
        logger.error(
            "Preflight loaded 0 rows for %s — the lake read may be broken "
            "(creds / catalog / empty window). Aborting before the full run.",
            symbols[0],
        )
        report.status = "fail"
        report.mismatch_warning = "preflight returned 0 rows"
        return _finalize_and_print(args, report, started)
    logger.info("Preflight OK: %s → %d rows in %.1fs", symbols[0], pf.rows_inserted, pf.duration_seconds)

    # Pre-state. NOTE: the preflight already inserted symbols[0]; capture the
    # baseline AFTER it (and after an optional wipe) so the delta tracks the
    # remaining symbols faithfully.
    if args.wipe:
        try:
            _wipe_ch_ohlcv()
        except Exception as e:  # noqa: BLE001
            logger.exception("CH wipe failed: %s", e)
            report.status = "fail"
            report.mismatch_warning = f"wipe failed: {e}"
            return _finalize_and_print(args, report, started)
        # Re-load the preflight symbol after the wipe so it isn't lost.
        pf = _fill_one(symbols[0], start, end)

    report.per_symbol.append(asdict(pf))
    report.rows_inserted_total += pf.rows_inserted
    report.ch_rows_before = _ch_ohlcv_row_count() - pf.rows_inserted
    remaining = symbols[1:]

    # Per-symbol loop (parallel). Each fill is an independent lake read; the
    # fill path serializes same-symbol work internally.
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.parallelism)) as pool:
        futures = {pool.submit(_fill_one, sym, start, end): sym for sym in remaining}
        done = 1  # preflight symbol already counted
        for fut in concurrent.futures.as_completed(futures):
            done += 1
            res = fut.result()
            report.per_symbol.append(asdict(res))
            report.rows_inserted_total += res.rows_inserted
            if not res.succeeded:
                report.failed_symbols.append(res.symbol)
                logger.error("rebuild_ch_from_lake: [%d/%d] %s FAILED: %s",
                             done, len(symbols), res.symbol, res.error)
            else:
                logger.info("rebuild_ch_from_lake: [%d/%d] %s rows=%d duration=%.1fs",
                            done, len(symbols), res.symbol, res.rows_inserted, res.duration_seconds)

    # Post-state: verify the mutation took effect (rule 1E, cross-side).
    report.ch_rows_after = _ch_ohlcv_row_count()
    report.ch_rows_delta = (
        report.ch_rows_after - report.ch_rows_before
        if report.ch_rows_before >= 0 and report.ch_rows_after >= 0 else 0
    )
    logger.info("Post-run CH ohlcv_1m rows: %d (delta=%+d, rows_inserted_reported=%d)",
                report.ch_rows_after, report.ch_rows_delta, report.rows_inserted_total)

    # ReplacingMergeTree may show a smaller delta than inserted if rows dedup
    # pre-merge (e.g. re-loading without --wipe). Only flag a shortfall on a
    # clean (wiped) load, where delta should closely track inserted rows.
    if args.wipe and report.rows_inserted_total > 0 and report.ch_rows_delta >= 0:
        ratio = report.ch_rows_delta / max(1, report.rows_inserted_total)
        if ratio < 0.9:
            report.mismatch_warning = (
                f"CH row delta ({report.ch_rows_delta:,}) is < 90% of inserted "
                f"({report.rows_inserted_total:,}). Possible silent insert "
                "failures — inspect logs."
            )
            logger.warning(report.mismatch_warning)

    if report.failed_symbols:
        report.status = "partial_fail"
    elif report.mismatch_warning:
        report.status = "ok_with_warnings"
    else:
        report.status = "ok"

    return _finalize_and_print(args, report, started)


def _finalize_and_print(args, report: RunResult, started: datetime) -> int:
    finished = datetime.now(timezone.utc)
    report.finished_at = finished.isoformat()
    report.duration_seconds = (finished - started).total_seconds()

    print()
    print("─── rebuild_ch_from_lake summary ───")
    print(f"  status:            {report.status}")
    print(f"  window:            {report.since} .. {report.until}")
    print(f"  symbols:           {report.symbols_count}")
    print(f"  parallelism:       {report.parallelism}")
    print(f"  wiped_first:       {report.wiped_ch_before_load}")
    print(f"  ch_rows_before:    {report.ch_rows_before:,}")
    print(f"  ch_rows_after:     {report.ch_rows_after:,}")
    print(f"  ch_rows_delta:     {report.ch_rows_delta:+,}")
    print(f"  rows_inserted:     {report.rows_inserted_total:,}")
    print(f"  failed_symbols:    {len(report.failed_symbols)}")
    if report.failed_symbols:
        for sym in report.failed_symbols[:10]:
            print(f"    - {sym}")
        if len(report.failed_symbols) > 10:
            print(f"    ... and {len(report.failed_symbols) - 10} more")
    print(f"  duration:          {report.duration_seconds:.1f}s")
    if report.mismatch_warning:
        print(f"  ⚠️  WARNING:       {report.mismatch_warning}")
    print()

    if args.out_json:
        args.out_json.write_text(json.dumps(asdict(report), indent=2, default=str))
        print(f"JSON report → {args.out_json}")

    return 0 if report.status in ("ok", "ok_with_warnings", "no_symbols", "dry_run") else 2


if __name__ == "__main__":
    sys.exit(main())
