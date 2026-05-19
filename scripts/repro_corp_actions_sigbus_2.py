#!/usr/bin/env python3
"""SIGBUS reproducer step 2 — pull the REAL 2020 payload, then try
upserting progressively larger slices to find the threshold OR a bad
row.

Strategy:
  1. Pull the actual 1236 Polygon splits for 2020 (same call the
     production backfill makes).
  2. Dedupe (16 collapses expected).
  3. Convert to Arrow (the production path).
  4. Try upserting in slices of size 1, 10, 100, 500, 1000, 1220.
  5. Print which slice succeeds and which crashes.

If a small slice (1 or 10) crashes, the bug is in the payload data
itself (a malformed row). If only the big slice (1000+) crashes, it's
a memory/threshold issue.

Synthetic-tagged rows so we don't pollute production identifiers —
we'll suffix every symbol with "_REPROZZZ".
"""
from __future__ import annotations

import asyncio
import sys
import logging
import os
from datetime import date
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)


async def amain() -> int:
    from app.providers.polygon_corp_actions import PolygonCorpActionsClient
    from app.services.iceberg_catalog import get_catalog
    from app.services.bronze.schemas import bronze_table_id
    from app.services.silver.corp_actions.polygon_ingest import (
        PolygonCorpActionsBronzeIngest,
    )
    from app.services.silver.schemas import CorpAction

    print("=== Step A: pull 2020 splits from Polygon ===", flush=True)
    client = PolygonCorpActionsClient.from_settings()
    actions = await client.collect_splits(
        since=date(2020, 1, 1), until=date(2020, 12, 31),
    )
    print(f"  pulled {len(actions)} splits", flush=True)

    print("=== Step B: dedupe ===", flush=True)
    deduped, n_collapsed = PolygonCorpActionsBronzeIngest._dedupe_actions(actions)
    print(f"  deduped={len(deduped)} collapsed={n_collapsed}", flush=True)

    # Re-tag every symbol to NOT collide with real data. Keep
    # everything else identical so we trigger the same code path.
    tagged: list[CorpAction] = []
    for a in deduped:
        tagged.append(
            CorpAction(
                symbol=a.symbol + "_REPROZZZ",
                ex_date=a.ex_date,
                action_type=a.action_type,
                factor=a.factor,
                cash_amount=a.cash_amount,
                announced_at=a.announced_at,
                source_provider=a.source_provider,
            )
        )

    # Try upserting in progressively larger slices.
    slice_sizes = [1, 10, 100, 500, 1000, len(tagged)]
    print(f"=== Step C: scaled upsert (sizes={slice_sizes}) ===", flush=True)

    cat = get_catalog()

    for n in slice_sizes:
        sample = tagged[:n]
        print(f"\n--- trying slice size {n} ---", flush=True)
        arrow = PolygonCorpActionsBronzeIngest._actions_to_arrow(
            sample, ingestion_run_id=f"REPRO_SLICE_{n}",
        )
        print(f"  arrow built: {arrow.num_rows} rows, "
              f"{arrow.nbytes:,} bytes", flush=True)

        # Append, not upsert — keeps memory lower and is what we
        # ultimately want (bronze idempotency = silver dedups).
        tbl = cat.load_table(bronze_table_id("polygon_corp_actions"))
        print(f"  pre snapshot_id={tbl.current_snapshot().snapshot_id}",
              flush=True)
        try:
            tbl.upsert(arrow)
            tbl2 = cat.load_table(bronze_table_id("polygon_corp_actions"))
            print(f"  upsert OK; post snapshot_id="
                  f"{tbl2.current_snapshot().snapshot_id}", flush=True)
        except Exception as e:
            print(f"  upsert RAISED: {type(e).__name__}: {e}", flush=True)
            return 2

    print("\nALL SLICES OK — no SIGBUS reproduced with REPROZZZ-tagged rows.",
          flush=True)
    print("This means the SIGBUS is NOT triggered by row count OR any "
          "specific value pattern within Polygon's 2020 splits.", flush=True)
    print("Likely the SIGBUS happens when the same identifier already "
          "exists in the table (the upsert merge path).", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(amain()))
