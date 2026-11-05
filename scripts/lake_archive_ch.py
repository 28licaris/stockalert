#!/usr/bin/env python3
"""Archive minute bars that ClickHouse holds and the lake does not.

ClickHouse bars tagged with a Schwab source (live stream, REST tip-fill) have no
other copy once Schwab's ~48-day minute history rolls off. This finds every such
bar missing from the lake (exact (symbol, timestamp) set difference per UTC day)
and, with --apply, appends them to equities.schwab_universe /
futures.schwab_futures, then re-verifies from the lake side.

    # What is missing? (writes nothing)
    poetry run python scripts/lake_archive_ch.py --family all

    # Archive it, then re-verify:
    poetry run python scripts/lake_archive_ch.py --family all --apply

    # Narrow window / machine-readable report:
    poetry run python scripts/lake_archive_ch.py --since 2026-06-25 --until 2026-07-05 --out-json /tmp/r.json

Exit codes: 0 = nothing missing (or everything archived and verified),
            3 = dry run found gaps,  2 = an error, or a write that did not verify.

Run it from the machine whose ClickHouse holds the data, and BEFORE wiping or
replacing that ClickHouse. Spec: docs/operations/deployment_dev.md and
app/services/lake_integrity/README.md. Standards: docs/standards/coding.md.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.lake_integrity.schemas import FAMILIES  # noqa: E402
from app.services.lake_integrity.service import LakeArchive  # noqa: E402

log = logging.getLogger("lake_archive_ch")


def _parse_day(s: str) -> date:
    return date.fromisoformat(s)


def _preflight(families) -> bool:
    """Fail fast (a full run takes minutes): ClickHouse answers and every lake table opens."""
    try:
        from app.db.client import get_client
        from app.services.iceberg_catalog import get_catalog

        get_client().query("SELECT 1")
        catalog = get_catalog()
        for fam in families:
            snap = catalog.load_table(fam.lake_table).current_snapshot()
            log.info("preflight: %s ok (current snapshot %s)", fam.lake_table, snap.snapshot_id if snap else None)
        return True
    except Exception as exc:  # noqa: BLE001 - reported with the fix, then a non-zero exit
        log.exception("PREFLIGHT FAILED: %s", exc)
        print(f"preflight failed: {exc}\nCheck ClickHouse is up, STOCK_LAKE_BUCKET is set and AWS_PROFILE=stock-lake.", file=sys.stderr)
        return False


def main(argv: list[str] | None = None) -> int:
    today = datetime.now(timezone.utc).date()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--family", choices=[*FAMILIES, "all"], default="all")
    ap.add_argument("--since", type=_parse_day, default=today - timedelta(days=200),
                    help="first UTC day to check (default: 200 days ago)")
    ap.add_argument("--until", type=_parse_day, default=today, help="last UTC day to check (default: today)")
    ap.add_argument("--apply", action="store_true", help="append the missing bars (default: dry run)")
    ap.add_argument("--out-json", type=Path, help="write the full report here")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    families = list(FAMILIES.values()) if args.family == "all" else [FAMILIES[args.family]]
    if not _preflight(families):
        return 2
    archive = LakeArchive.from_settings()

    reports = []
    for fam in families:
        reports.append(archive.run(fam, since=args.since, until=args.until, apply=args.apply))

    print(f"\n{'APPLY' if args.apply else 'DRY RUN'}  {args.since} .. {args.until}")
    for r in reports:
        print(f"\n[{r.family}] status={r.status}  days_checked={r.days_checked}  "
              f"days_with_gaps={r.days_with_gaps}  missing={r.missing_total}  archived={r.archived_total}  "
              f"remaining={r.remaining}")
        for g in r.days:
            if g.missing or g.error:
                print(f"    {g.day}  ch={g.ch_bars:<8} missing={g.missing:<8} archived={g.archived:<8}"
                      + (f"  ERROR {g.error}" if g.error else ""))
    if args.out_json:
        args.out_json.write_text(json.dumps([r.model_dump(mode="json") for r in reports], indent=2))
        print(f"\nreport written to {args.out_json}")

    if any(r.status == "error" for r in reports):
        return 2
    if any(r.status == "gaps_found" for r in reports):
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
