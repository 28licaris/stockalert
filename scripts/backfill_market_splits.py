"""
One-time backfill of ``equities.market_splits`` from the splits already in
``equities.market_corp_actions``.

After this runs, the Polygon corp-actions ingest keeps market_splits current
(dual-write during the overlap cycle) and the read-time adjustment sources
splits from market_splits — never scanning the ~3M-row dividend table again.
See docs/market_splits_spec.md.

Idempotent: upserts on the (symbol, ex_date) identifier, so re-runs converge.

    AWS_PROFILE=stock-lake poetry run python scripts/backfill_market_splits.py
"""
from __future__ import annotations

import logging
import time

import pyarrow.compute as pc
from pyiceberg.expressions import EqualTo

from app.services.equities.schemas import equities_table_id
from app.services.equities.tables import ensure_market_splits
from app.services.iceberg_catalog import get_catalog
from app.services.iceberg_safe_upsert import chunked_upsert

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("backfill_market_splits")

_SPLIT_COLS = ("symbol", "ex_date", "factor", "source_provider",
               "ingestion_ts", "ingestion_run_id")


def main() -> int:
    cat = get_catalog()
    splits_tbl = ensure_market_splits(cat)

    # Read every split from the dividend-dominated corp-actions table — the
    # ~79 s full scan, paid exactly ONCE here.
    t0 = time.time()
    ca = cat.load_table(equities_table_id("market_corp_actions"))
    arr = ca.scan(
        row_filter=EqualTo("action_type", "split"),
        selected_fields=_SPLIT_COLS,
    ).to_arrow()
    log.info("read %d split rows from market_corp_actions in %.1fs",
             arr.num_rows, time.time() - t0)

    # market_splits.factor is required → drop any null-factor rows.
    mask = pc.is_valid(arr.column("factor"))
    arr = arr.filter(mask)

    # Cast to market_splits' exact Arrow schema (field IDs/types/order) so
    # PyIceberg's upsert schema check passes — corp_actions carries different
    # field IDs + extra columns.
    import pyarrow as pa
    target = splits_tbl.schema().as_arrow()
    out = pa.table(
        {f.name: arr.column(f.name).cast(f.type) for f in target},
        schema=target,
    )
    log.info("upserting %d splits into market_splits", out.num_rows)

    res = chunked_upsert(splits_tbl, out, log_label="market_splits")
    log.info("upsert done: %s", res)

    # Verify.
    splits_tbl = cat.load_table(equities_table_id("market_splits"))
    total = splits_tbl.scan(selected_fields=("symbol",)).to_arrow().num_rows
    log.info("market_splits now holds %d rows", total)
    print(f"BACKFILL OK: market_splits rows={total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
