#!/usr/bin/env python3
"""Minimal reproducer for the PyIceberg upsert SIGBUS on
bronze.polygon_corp_actions.

We isolate each step of the write path so we can see EXACTLY which
line crashes (vs the production code path which dies mid-function and
loses the breadcrumbs).

Steps:
  1. Load table fresh
  2. Build a 2-row Arrow batch from hand-rolled CorpActions
  3. Run `_dedupe_actions` (pure python, should never SIGBUS)
  4. Run `_actions_to_arrow` (PyArrow conversion)
  5. Run `table.append(arrow)` — append-only, simpler than upsert
  6. Run `table.upsert(arrow)` — the failing call in production

Each step prints "STEP N: OK" on success. If the script dies, the
last line of output tells us where.
"""
from __future__ import annotations

import sys
import logging
from datetime import date, datetime, timezone
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


def _print_step(n: int, what: str, status: str = "START") -> None:
    print(f"--- STEP {n} {status}: {what} ---", flush=True)


def main() -> int:
    _print_step(0, "imports", "START")
    from app.services.iceberg_catalog import get_catalog
    from app.services.bronze.schemas import bronze_table_id
    from app.services.silver.corp_actions.polygon_ingest import (
        PolygonCorpActionsBronzeIngest,
    )
    from app.services.silver.schemas import CorpAction
    _print_step(0, "imports", "OK")

    _print_step(1, "load_table", "START")
    cat = get_catalog()
    tbl = cat.load_table(bronze_table_id("polygon_corp_actions"))
    snap = tbl.current_snapshot()
    snap_id_pre = str(snap.snapshot_id) if snap else None
    rows_pre = int(snap.summary.additional_properties.get("total-records", 0)) if snap else 0
    print(f"  pre-state: snapshot_id={snap_id_pre} rows={rows_pre}")
    _print_step(1, "load_table", "OK")

    _print_step(2, "build_2_synthetic_actions", "START")
    # Two rows with synthetic-but-unique identifiers — won't collide
    # with any real data, won't pollute downstream (we'll roll back
    # by skipping the upsert in dry-run).
    actions = [
        CorpAction(
            symbol="ZZZTEST1",
            ex_date=date(2099, 1, 1),
            action_type="split",
            factor=2.0,
            source_provider="polygon",
        ),
        CorpAction(
            symbol="ZZZTEST2",
            ex_date=date(2099, 1, 2),
            action_type="cash_dividend",
            cash_amount=0.01,
            source_provider="polygon",
        ),
    ]
    print(f"  built {len(actions)} synthetic actions")
    _print_step(2, "build_2_synthetic_actions", "OK")

    _print_step(3, "dedupe_actions", "START")
    deduped, n_collapsed = PolygonCorpActionsBronzeIngest._dedupe_actions(actions)
    print(f"  deduped={len(deduped)} collapsed={n_collapsed}")
    _print_step(3, "dedupe_actions", "OK")

    _print_step(4, "actions_to_arrow", "START")
    arrow = PolygonCorpActionsBronzeIngest._actions_to_arrow(
        deduped, ingestion_run_id="REPRO_TEST",
    )
    print(f"  arrow.num_rows={arrow.num_rows}")
    print(f"  arrow.schema_matches_iceberg=...")
    _print_step(4, "actions_to_arrow", "OK")

    # Bail out before mutating production if --dry-run
    if "--dry-run" in sys.argv:
        print("--dry-run set — skipping append/upsert (table state unchanged)")
        return 0

    _print_step(5, "table_append_2_rows", "START")
    try:
        tbl.append(arrow)
        print("  append returned without raising")
    except Exception as e:
        print(f"  append RAISED: {type(e).__name__}: {e}")
        raise
    # Reload to check
    tbl2 = cat.load_table(bronze_table_id("polygon_corp_actions"))
    snap2 = tbl2.current_snapshot()
    print(f"  post-append snapshot_id={snap2.snapshot_id}")
    _print_step(5, "table_append_2_rows", "OK")

    _print_step(6, "table_upsert_same_2_rows_should_be_noop", "START")
    # Upserting the SAME rows again — they're already there (we just
    # appended). Should be a no-op or update-in-place.
    try:
        result = tbl2.upsert(arrow)
        print(f"  upsert returned: rows_updated={result.rows_updated} "
              f"rows_inserted={result.rows_inserted}")
    except Exception as e:
        print(f"  upsert RAISED: {type(e).__name__}: {e}")
        raise
    _print_step(6, "table_upsert_same_2_rows_should_be_noop", "OK")

    print()
    print("All steps OK. The SIGBUS is specific to the production payload,")
    print("not the upsert mechanism itself. Likely candidates:")
    print("  - row count threshold (try with 100, 500, 1236 rows)")
    print("  - a specific row value triggers a parquet writer bug")
    print("  - memory state at the moment of upsert")
    return 0


if __name__ == "__main__":
    sys.exit(main())
