#!/usr/bin/env python3
"""Build a ClickHouse from scratch: schema -> state -> bars -> futures -> daily -> verify.

One command to stand ClickHouse up on a new machine (or after a wipe) using only
what is in S3: the nightly state snapshot (everything the lake cannot rebuild,
plus the per-symbol bar coverage recorded from the source ClickHouse) and the
Iceberg lake (every bar).

    poetry run python scripts/ch_bootstrap.py --dry-run               # print the plan, change nothing
    poetry run python scripts/ch_bootstrap.py --state-mode replace    # a fresh box whose startup seeded a few tables
    poetry run python scripts/ch_bootstrap.py --steps verify          # re-check an existing ClickHouse

Steps (each is idempotent and can be run alone with --steps):
  schema   create the standard tables (app.db.init.init_schema)
  state    restore the snapshot tables; --state-mode replace TRUNCATEs a table that already has rows
  bars     rebuild ohlcv_1m from the lake, each symbol over the window its recorded history needs
  futures  rebuild futures_ohlcv_1m from the futures lake
  daily    build ohlcv_daily from the lake ONLY if the snapshot did not restore it
  verify   compare the result with the recorded coverage; exit 2 on any shortfall

Exit codes: 0 verified, 2 a step failed or verification found a shortfall.
Spec: app/services/lake_integrity/README.md; runbook: docs/operations/runbook.md.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings  # noqa: E402
from app.services.lake_integrity.bootstrap import rebuild_windows, split_window, verify_coverage  # noqa: E402
from app.services.lake_integrity.snapshot import StateSnapshot  # noqa: E402

log = logging.getLogger("ch_bootstrap")
ALL_STEPS = ["schema", "state", "bars", "futures", "daily", "verify"]


def _lake_history_start() -> date:
    try:
        return date.fromisoformat(settings.lake_history_start)
    except (TypeError, ValueError):
        return date(2021, 1, 4)


def _run(cmd: list[str]) -> int:
    log.info("$ %s", " ".join(cmd))
    return subprocess.run(cmd, cwd=ROOT).returncode


def step_schema(args, ctx) -> str:
    from app.db.init import init_schema

    init_schema()
    return "init_schema ok"


def step_state(args, ctx) -> str:
    rep = ctx["snap"].restore(args.snapshot, mode=args.state_mode, restore_files=args.files)
    for t in rep.tables:
        log.info("state %-28s %-18s rows=%d %s", t.name, t.status, t.rows, t.error or "")
    skipped = [t.name for t in rep.tables if t.status == "skipped_nonempty"]
    if not rep.ok:
        raise RuntimeError(f"state restore failed: {[t.name for t in rep.tables if t.status == 'error']} {rep.errors}")
    note = f"; SKIPPED non-empty (use --state-mode replace): {skipped}" if skipped else ""
    return f"{sum(1 for t in rep.tables if t.status == 'restored')} table(s) restored{note}"


def step_bars(args, ctx) -> str:
    cov = ctx["manifest"].coverage.get("ohlcv_1m", [])
    if not cov:
        raise RuntimeError("the snapshot has no ohlcv_1m coverage; cannot decide what to rebuild "
                           "(run: scripts/rebuild_ch_from_lake.py --symbols active)")
    until = max(c.last for c in cov).date()
    failures = 0
    total = 0
    for since, symbols in rebuild_windows(cov, _lake_history_start()).items():
        total += len(symbols)
        # Bounded segments keep per-worker memory small (a 20-year symbol in one call is ~3.5 GB).
        for seg_start, seg_end in split_window(since, until, args.max_years):
            cmd = [sys.executable, "scripts/rebuild_ch_from_lake.py", "--symbols", ",".join(symbols),
                   "--since", seg_start.isoformat(), "--until", seg_end.isoformat(),
                   "--parallelism", str(args.parallelism), "--batch-symbols", str(args.batch_symbols)]
            log.info("bars: segment_start=%s segment_end=%s symbols=%d", seg_start, seg_end, len(symbols))
            if _run(cmd) != 0:
                failures += 1
                log.error("bars: the loader reported failures for %s..%s", seg_start, seg_end)
    if failures:
        raise RuntimeError(f"{failures} rebuild window(s) failed (see the loader output above)")
    return f"{total} symbol(s) rebuilt through {until}"


def step_futures(args, ctx) -> str:
    from app.services.futures.lake_to_ch_fill import fill_ch_from_futures_lake_sync

    cov = ctx["manifest"].coverage.get("futures_ohlcv_1m", [])
    empty = []
    rows = 0
    for c in cov:
        n = fill_ch_from_futures_lake_sync(c.symbol, c.first, c.last + timedelta(days=1))
        rows += n
        if n == 0:
            empty.append(c.symbol)
    if empty:
        raise RuntimeError(f"no futures rows loaded for {empty}")
    return f"{len(cov)} root(s), {rows} rows"


def step_daily(args, ctx) -> str:
    from app.db.client import get_client

    try:
        n = int(get_client().query("SELECT count() FROM ohlcv_daily").result_rows[0][0])
    except Exception as exc:  # noqa: BLE001 - an absent table counts as empty, but say so
        log.info("daily: ohlcv_daily not readable (%s); treating it as empty", exc)
        n = 0
    if n > 0:
        return f"ohlcv_daily already restored from the snapshot ({n} rows); nothing to build"
    for cmd in (["scripts/build_ohlcv_daily.py", "--create"], ["scripts/build_ohlcv_daily.py", "--universe"],
                ["scripts/refresh_ohlcv_daily.py"]):
        if _run([sys.executable, *cmd]) != 0:
            raise RuntimeError(f"{' '.join(cmd)} failed")
    return "ohlcv_daily built from the lake"


def step_verify(args, ctx) -> str:
    from app.services.lake_integrity.adapters import ClickHouseStateClient
    from app.services.lake_integrity.schemas import CoverageRow

    ch = ClickHouseStateClient()
    manifest = ctx["manifest"]
    problems = []
    for table, expected in manifest.coverage.items():
        actual = [CoverageRow(symbol=s, first=a, last=b, rows=n) for s, a, b, n in ch.coverage(settings.clickhouse_database, table)]
        found = verify_coverage(expected, actual)
        log.info("verify %-18s expected_symbols=%d found_symbols=%d problems=%d", table, len(expected), len(actual), len(found))
        problems += [(table, p) for p in found]
    for t in manifest.tables:
        if t.rows > 0 and ch.row_count(settings.clickhouse_database, t.name) == 0:
            problems.append((t.name, type("P", (), {"symbol": "-", "kind": "empty_state_table", "detail": f"snapshot had {t.rows} rows"})()))
    for table, p in problems[:40]:
        log.error("verify %s %s %s: %s", table, p.symbol, p.kind, p.detail)
    if problems:
        raise RuntimeError(f"verification found {len(problems)} shortfall(s)")
    return "every symbol and state table holds at least what the snapshot recorded"


STEPS = {"schema": step_schema, "state": step_state, "bars": step_bars,
         "futures": step_futures, "daily": step_daily, "verify": step_verify}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--steps", default=",".join(ALL_STEPS), help=f"comma-separated subset of {ALL_STEPS}")
    ap.add_argument("--snapshot", default="latest")
    ap.add_argument("--state-mode", choices=["empty_only", "replace"], default="empty_only")
    ap.add_argument("--files", action="store_true", help="also restore the paper-trading files into data/paper")
    ap.add_argument("--parallelism", type=int, default=2, help="symbols loaded at once (memory-bound: keep small)")
    ap.add_argument("--max-years", type=int, default=6, help="longest window loaded in one call (bounds memory)")
    ap.add_argument("--batch-symbols", type=int, default=8,
                    help="symbols per lake read (bulk path: one read + Arrow inserts per batch; 1 = original per-symbol path)")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, change nothing")
    ap.add_argument("--out-json", type=Path)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

    steps = [s.strip() for s in args.steps.split(",") if s.strip()]
    bad = [s for s in steps if s not in STEPS]
    if bad:
        ap.error(f"unknown step(s) {bad}; choose from {ALL_STEPS}")

    snap = StateSnapshot.from_settings(paper_dir=ROOT / "data" / "paper")
    manifest = snap.manifest(args.snapshot)
    ctx = {"snap": snap, "manifest": manifest}
    cov = manifest.coverage.get("ohlcv_1m", [])
    print(f"snapshot {manifest.snapshot_id} (taken {manifest.created_at:%Y-%m-%d %H:%M} UTC): "
          f"{len(manifest.tables)} state tables, {len(manifest.files)} files, "
          f"{len(cov)} equity symbols, {len(manifest.coverage.get('futures_ohlcv_1m', []))} futures roots")
    if args.state_mode == "replace":
        print("WARNING: --state-mode replace TRUNCATES any state table that already has rows, then loads the snapshot.")
    if cov:
        groups = rebuild_windows(cov, _lake_history_start())
        print("equity rebuild windows: " + ", ".join(f"{d}: {len(s)} symbol(s)" for d, s in groups.items()))
    print("steps: " + " -> ".join(steps))
    if args.dry_run:
        return 0

    results, failed = [], False
    for name in steps:
        if failed and name != "verify":
            results.append({"step": name, "ok": False, "detail": "skipped: an earlier step failed"})
            log.warning("STEP %-8s SKIPPED (an earlier step failed)", name)
            continue
        try:
            detail = STEPS[name](args, ctx)
            results.append({"step": name, "ok": True, "detail": detail})
            log.info("STEP %-8s OK  %s", name, detail)
        except Exception as exc:  # noqa: BLE001 - reported; later steps are skipped, verify still runs
            failed = True
            results.append({"step": name, "ok": False, "detail": f"{type(exc).__name__}: {exc}"})
            log.exception("STEP %-8s FAILED  %s", name, exc)
    print("\nSUMMARY")
    for r in results:
        print(f"  {'OK    ' if r['ok'] else 'FAILED'} {r['step']:<8} {r['detail']}")
    if args.out_json:
        args.out_json.write_text(json.dumps(results, indent=2))
    return 2 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
