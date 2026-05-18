#!/usr/bin/env python3
"""
Universal bronze-layer audit runner.

Iterates every registered check in `app.services.bronze.audit` across
every bronze table in `BRONZE_TABLES_TO_AUDIT`, prints a human-readable
report, and (optionally) writes a JSON report for CI consumption.

**Run:**

    poetry run python scripts/audit_bronze.py

**Pick a specific check:**

    poetry run python scripts/audit_bronze.py --check adjustment_status

**Restrict to one table:**

    poetry run python scripts/audit_bronze.py --table polygon_minute

**JSON report:**

    poetry run python scripts/audit_bronze.py --out-json audit.json

See `app/services/bronze/audit/README.md` for the framework docs.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.services.bronze.audit import (  # noqa: E402
    AuditResult,
    AuditSeverity,
    AuditStatus,
    build_all_checks,
    build_check,
    list_registered_checks,
)
from app.services.bronze.audit.base import BRONZE_TABLES_TO_AUDIT  # noqa: E402

logger = logging.getLogger(__name__)


_STATUS_GLYPHS = {
    AuditStatus.OK: "🟢 OK",
    AuditStatus.WARN: "🟡 WARN",
    AuditStatus.FAIL: "🔴 FAIL",
    AuditStatus.SKIPPED: "⚪ SKIP",
}


def _format_results(results: list[AuditResult]) -> str:
    """Pretty terminal table."""
    if not results:
        return "(no results)"
    header = (
        f"{'STATUS':<8} {'TABLE':<25} {'CHECK':<22} MESSAGE"
    )
    lines = [header, "-" * len(header)]
    for r in results:
        glyph = _STATUS_GLYPHS.get(r.status, r.status)
        lines.append(f"{glyph:<8} {r.table:<25} {r.check:<22} {r.message}")
        if r.error:
            lines.append(f"         ↳ error: {r.error}")
    return "\n".join(lines)


def _exit_code(results: list[AuditResult], strict: bool) -> int:
    """0 if all OK/SKIPPED; non-zero if any FAIL (always) or WARN (if strict)."""
    has_fail = any(r.status == AuditStatus.FAIL for r in results)
    has_warn = any(r.status == AuditStatus.WARN for r in results)
    if has_fail:
        return 2
    if strict and has_warn:
        return 1
    return 0


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    p.add_argument(
        "--check",
        choices=list_registered_checks(),
        default=None,
        help="Run a single check (default: all registered checks).",
    )
    p.add_argument(
        "--table",
        choices=BRONZE_TABLES_TO_AUDIT,
        default=None,
        help="Restrict to one bronze table (default: all).",
    )
    p.add_argument(
        "--list-checks",
        action="store_true",
        help="Print registered checks + tables and exit.",
    )
    p.add_argument(
        "--out-json",
        type=Path,
        default=None,
        help="Write structured report to this path.",
    )
    p.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on WARN as well as FAIL.",
    )
    return p


def main() -> int:
    args = _build_parser().parse_args()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )

    if args.list_checks:
        print("Registered checks:")
        for c in list_registered_checks():
            print(f"  {c}")
        print(f"\nBronze tables: {BRONZE_TABLES_TO_AUDIT}")
        return 0

    # Select checks
    if args.check:
        checks = [build_check(args.check)]
    else:
        checks = build_all_checks()

    # Select tables
    tables = [args.table] if args.table else BRONZE_TABLES_TO_AUDIT

    logger.info(
        "Auditing bronze: %d check(s) × %d table(s)",
        len(checks), len(tables),
    )

    all_results: list[AuditResult] = []
    for check in checks:
        for table_name in tables:
            try:
                all_results.extend(check.run(table_name))
            except Exception as e:
                # Defensive — checks promise not to raise but we won't
                # crash the runner if one does.
                all_results.append(
                    AuditResult(
                        check=check.check_name,
                        table=table_name,
                        status=AuditStatus.FAIL,
                        severity=AuditSeverity.FAIL,
                        message="check raised — bug in audit code",
                        error=f"{type(e).__name__}: {e}",
                    )
                )

    print()
    print(_format_results(all_results))
    print()

    # Summary
    counts = {s: 0 for s in (AuditStatus.OK, AuditStatus.WARN, AuditStatus.FAIL, AuditStatus.SKIPPED)}
    for r in all_results:
        counts[r.status] = counts.get(r.status, 0) + 1
    print(
        f"Summary: 🟢 {counts.get(AuditStatus.OK, 0)} ok  "
        f"🟡 {counts.get(AuditStatus.WARN, 0)} warn  "
        f"🔴 {counts.get(AuditStatus.FAIL, 0)} fail  "
        f"⚪ {counts.get(AuditStatus.SKIPPED, 0)} skip"
    )

    if args.out_json:
        payload = {
            "audited_at": datetime.now(timezone.utc).isoformat(),
            "tables": tables,
            "checks": [c.check_name for c in checks],
            "summary": counts,
            "results": [asdict(r) for r in all_results],
        }
        args.out_json.write_text(json.dumps(payload, indent=2, default=str))
        print(f"JSON report → {args.out_json}")

    return _exit_code(all_results, strict=args.strict)


if __name__ == "__main__":
    sys.exit(main())
