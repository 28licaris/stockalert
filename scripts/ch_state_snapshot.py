#!/usr/bin/env python3
"""Back up / restore the ClickHouse state the lake cannot rebuild.

Exports every ClickHouse table except the large rebuildable bar tables
(ohlcv_1m, futures_ohlcv_1m, ohlcv_1m_candidates, ohlcv_hourly) to
s3://$STOCK_LAKE_BUCKET/ch_state/<snapshot_id>/ as Parquet, plus the paper-trading
files under data/paper, plus a manifest with each table's DDL and checksum.

    poetry run python scripts/ch_state_snapshot.py export
    poetry run python scripts/ch_state_snapshot.py list
    poetry run python scripts/ch_state_snapshot.py restore                       # latest, only into EMPTY tables
    poetry run python scripts/ch_state_snapshot.py restore --mode replace        # truncate + load (explicit)
    poetry run python scripts/ch_state_snapshot.py restore --target-database stocks_restore_test --tables paper_state
    poetry run python scripts/ch_state_snapshot.py restore --files               # also restore data/paper files
    poetry run python scripts/ch_state_snapshot.py prune --keep 14

Exit codes: 0 ok, 2 incomplete export / failed restore. Module:
app/services/lake_integrity/README.md. Standards: docs/standards/coding.md.
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.lake_integrity.snapshot import StateSnapshot  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--paper-dir", type=Path, default=Path("data/paper"), help="paper-trading files (default data/paper)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("--force", action="store_true",
                   help="publish even if a table shrank >50%% vs the previous snapshot (default: refuse)")
    sub.add_parser("list")
    r = sub.add_parser("restore")
    r.add_argument("--snapshot", default="latest")
    r.add_argument("--mode", choices=["empty_only", "replace"], default="empty_only")
    r.add_argument("--tables", help="comma-separated subset")
    r.add_argument("--target-database", help="restore into another database (rehearsal)")
    r.add_argument("--files", action="store_true", help="also restore the paper-trading files")
    r.add_argument("--overwrite-files", action="store_true")
    p = sub.add_parser("prune")
    p.add_argument("--keep", type=int, default=14)
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    snap = StateSnapshot.from_settings(paper_dir=args.paper_dir)

    if args.cmd == "export":
        m = snap.export(force=args.force)
        print(f"\nsnapshot {m.snapshot_id}  complete={m.complete}")
        for t in m.tables:
            print(f"  {t.name:<28} rows={t.rows:<10} bytes={t.bytes}")
        for f in m.files:
            print(f"  file {f.name:<32} bytes={f.bytes}")
        print(f"  excluded (rebuilt from the lake): {', '.join(m.excluded)}")
        for k, v in m.failed.items():
            print(f"  FAILED {k}: {v}")
        return 0 if m.complete else 2
    if args.cmd == "list":
        latest = snap.latest_id()
        print(f"latest: {latest}")
        if latest:
            m = snap.manifest(latest)
            print(f"  created {m.created_at}  tables={len(m.tables)}  rows={sum(t.rows for t in m.tables)}  files={len(m.files)}")
        return 0
    if args.cmd == "restore":
        rep = snap.restore(
            args.snapshot, mode=args.mode, tables=args.tables.split(",") if args.tables else None,
            target_database=args.target_database, restore_files=args.files, overwrite_files=args.overwrite_files,
        )
        print(f"\nrestore of {rep.snapshot_id}  ok={rep.ok}")
        for t in rep.tables:
            print(f"  {t.name:<28} {t.status:<18} rows={t.rows}" + (f"  {t.error}" if t.error else ""))
        if args.files:
            print(f"  files restored={rep.files_restored} skipped={rep.files_skipped}")
        for e in rep.errors:
            print(f"  ERROR {e}")
        return 0 if rep.ok else 2
    if args.cmd == "prune":
        removed = snap.prune(keep=args.keep)
        print(f"pruned {len(removed)} snapshot(s): {removed}")
        return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
