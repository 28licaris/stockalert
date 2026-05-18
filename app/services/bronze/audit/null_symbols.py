"""
Null-symbol audit.

A known data-quality finding from the Phase 1 import (BUILD_JOURNAL.md
2026-05-14): ~80k Polygon flat-file rows (0.0038% of 2.1B) carried
NULL symbol but valid OHLCV — they were filtered at the bronze
boundary. This check asserts those rows haven't snuck back in via a
later writer.

Also checks for empty-string symbols (which Iceberg's identifier
constraint wouldn't catch since "" is a valid string) and obviously
broken values (e.g. whitespace-only).
"""
from __future__ import annotations

from app.services.bronze.audit import register_check
from app.services.bronze.audit.base import (
    AuditResult,
    AuditSeverity,
    AuditStatus,
    safe_load_table,
)


@register_check("null_symbols")
class NullSymbolsCheck:
    """Count rows with missing/blank/whitespace-only symbol values."""

    check_name = "null_symbols"

    def run(self, table_name: str) -> list[AuditResult]:
        table, err = safe_load_table(table_name)
        if err:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.SKIPPED,
                    message="cannot load table",
                    error=err,
                )
            ]

        try:
            scan = table.scan(selected_fields=("symbol",))
            arrow = scan.to_arrow()
        except Exception as e:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.SKIPPED,
                    message=f"scan failed: {type(e).__name__}",
                    error=str(e),
                )
            ]

        if arrow.num_rows == 0:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.SKIPPED,
                    message="empty table; nothing to audit",
                )
            ]

        import pyarrow.compute as pc
        col = arrow["symbol"]

        # Three failure modes:
        # 1. Iceberg null
        null_count = pc.sum(pc.is_null(col)).as_py() or 0
        # 2. Empty string
        empty_count = pc.sum(pc.equal(col, "")).as_py() or 0
        # 3. Whitespace-only (catches typos like ' ' or '\t')
        try:
            stripped = pc.utf8_trim(col, characters=" \t\n\r")
            whitespace_count = pc.sum(
                pc.and_(
                    pc.not_equal(col, ""),
                    pc.equal(stripped, ""),
                )
            ).as_py() or 0
        except Exception:
            whitespace_count = 0

        total = arrow.num_rows
        bad_total = (null_count or 0) + (empty_count or 0) + (whitespace_count or 0)

        details = {
            "total_rows_scanned": total,
            "null_symbol_count": int(null_count),
            "empty_string_symbol_count": int(empty_count),
            "whitespace_only_symbol_count": int(whitespace_count),
            "bad_total": int(bad_total),
            "bad_pct": (bad_total / total) if total else 0.0,
        }

        if bad_total == 0:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.OK,
                    severity=AuditSeverity.INFO,
                    message=f"all {total:,} rows have valid symbol",
                    details=details,
                )
            ]

        # Any non-zero bad_total = fail. Bronze identifier
        # `(symbol, timestamp)` shouldn't allow nulls in symbol but the
        # historical import had this gap before we filtered.
        return [
            AuditResult(
                check=self.check_name,
                table=table_name,
                status=AuditStatus.FAIL,
                severity=AuditSeverity.FAIL,
                message=(
                    f"{bad_total:,} bad-symbol rows out of {total:,} "
                    f"({details['bad_pct']*100:.4f}%)"
                ),
                details=details,
            )
        ]
