#!/usr/bin/env python3
"""One-time migration: re-partition equities.schwab_universe to month-only.

Drops the `bucket(16, symbol)` partition field, leaving `month(timestamp)`
(+ the existing (symbol, timestamp) sort order). For a small recent-window
table (~hundreds of symbols) bucketing just fans every write across 16
files; month-only yields ~1 file/month and needs no compaction. See the
rationale in app/services/equities/schemas.py::SCHWAB_UNIVERSE_PARTITION.

Strategy (in-place, no drop_table — IAM-friendly + atomic):
  1. Read all rows -> Arrow; dump to a disk backup; verify the backup.
  2. update_spec: remove the `symbol_bucket` partition field.
  3. overwrite() the data back (sorted by symbol) -> written under the new
     month-only spec in one Iceberg commit. Old bucketed files become
     orphans (self-clean past snapshot retention; VACUUM to force).
  4. Verify row count unchanged + report active file count.

Safe to re-run: if the spec is already month-only it still round-trips the
data cleanly. Refuses to touch an empty table.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pyarrow.parquet as pq

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.services.equities.schemas import equities_table_id  # noqa: E402
from app.services.iceberg_catalog import get_catalog  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("migrate_schwab_month_only")

BACKUP = "/tmp/schwab_universe_backup.parquet"


def main() -> int:
    catalog = get_catalog()
    tid = equities_table_id("schwab_universe")
    table = catalog.load_table(tid)

    log.info("current partition spec: %s", table.spec())

    # 1. Read everything + back it up to disk.
    arrow = table.scan().to_arrow()
    n0 = arrow.num_rows
    log.info("read %d rows from %s", n0, tid)
    if n0 == 0:
        log.error("table is empty — refusing to migrate")
        return 1

    # Sort by (symbol, timestamp) so the rewritten files are symbol-clustered
    # (matches the table sort order → good Parquet row-group pruning).
    arrow = arrow.sort_by([("symbol", "ascending"), ("timestamp", "ascending")])

    pq.write_table(arrow, BACKUP)
    verify = pq.read_table(BACKUP)
    if verify.num_rows != n0:
        log.error("backup verify FAILED: %d != %d", verify.num_rows, n0)
        return 1
    log.info("backup verified: %s (%d rows)", BACKUP, verify.num_rows)

    # 2. Evolve the partition spec — drop the symbol bucket.
    field_names = [f.name for f in table.spec().fields]
    if "symbol_bucket" in field_names:
        log.info("removing 'symbol_bucket' partition field")
        with table.update_spec() as update:
            update.remove_field("symbol_bucket")
        table = catalog.load_table(tid)
        log.info("new partition spec: %s", table.spec())
    else:
        log.info("spec already has no 'symbol_bucket' — skipping evolution")

    # 3. Overwrite all data under the new spec (atomic).
    log.info("overwriting %d rows under month-only spec...", n0)
    table.overwrite(arrow)
    table = catalog.load_table(tid)

    # 4. Verify.
    n1 = table.scan().to_arrow().num_rows
    nfiles = sum(1 for _ in table.scan().plan_files())
    log.info("post-migration: rows=%d (was %d), active_files=%d", n1, n0, nfiles)
    if n1 != n0:
        log.error("ROW COUNT MISMATCH %d != %d — backup at %s", n1, n0, BACKUP)
        return 1

    log.info("✓ migration complete: rows=%d active_files=%d", n1, nfiles)
    print(f"DONE rows={n1} active_files={nfiles} backup={BACKUP}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
