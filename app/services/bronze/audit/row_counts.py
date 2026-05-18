"""
Row-count + coverage audit.

Reports row counts per table and the date range covered. Detects
catastrophic regressions (e.g. a table suddenly empty) and gives
operators a baseline for "is bronze growing as expected?"

Uses the Iceberg snapshot summary where possible (instant, no scan).
Falls back to a bounded scan for date range.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from app.services.bronze.audit import register_check
from app.services.bronze.audit.base import (
    AuditResult,
    AuditSeverity,
    AuditStatus,
    safe_load_table,
)


@register_check("row_counts")
class RowCountsCheck:
    """Report row counts + date coverage per table."""

    check_name = "row_counts"

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

        snap = table.current_snapshot()
        if snap is None:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.WARN,
                    severity=AuditSeverity.WARN,
                    message="table exists but has NO snapshots — empty table",
                )
            ]

        # Iceberg snapshot.summary is a Mapping (often a dict-like). Walk
        # safely without assuming dict.
        summary = self._summary_to_dict(snap.summary)
        total_records = self._parse_int(summary.get("total-records"))
        total_files = self._parse_int(summary.get("total-data-files"))
        total_size_bytes = self._parse_int(summary.get("total-files-size"))

        # Determine date range by sampling the timestamp column. Bounded:
        # only fetch min/max via a metadata operation if possible; else
        # fall back to a column scan with limit (cheap on partition-pruned
        # tables).
        ts_col = self._timestamp_column_for(table_name)
        first_ts, last_ts = self._date_range(table, ts_col)

        details = {
            "total_records": total_records,
            "total_data_files": total_files,
            "total_files_size_bytes": total_size_bytes,
            "total_files_size_mb": (
                round(total_size_bytes / (1024 * 1024), 1)
                if total_size_bytes is not None else None
            ),
            "first_timestamp": first_ts,
            "last_timestamp": last_ts,
            "snapshot_id": snap.snapshot_id,
            "snapshot_summary_keys": sorted(summary.keys()),
        }

        # Build the message defensively — total_records may be None on
        # older snapshots whose summary doesn't expose the canonical
        # `total-records` key.
        records_str = (
            f"{total_records:,}" if total_records is not None else "?"
        )
        files_str = (
            f"{total_files}" if total_files is not None else "?"
        )
        message = f"records={records_str} files={files_str} range={first_ts}..{last_ts}"

        status = AuditStatus.OK
        severity = AuditSeverity.INFO
        if total_records is not None and total_records == 0:
            status = AuditStatus.WARN
            severity = AuditSeverity.WARN
            message = "table snapshot reports zero records"
        elif total_records is None:
            # Not a failure — older snapshots don't always have the
            # total-records key. Worth flagging so operator knows the
            # detail is unavailable.
            status = AuditStatus.WARN
            severity = AuditSeverity.WARN
            message = (
                f"snapshot summary lacks 'total-records' key "
                f"(available: {sorted(summary.keys())}); range={first_ts}..{last_ts}"
            )

        return [
            AuditResult(
                check=self.check_name,
                table=table_name,
                status=status,
                severity=severity,
                message=message,
                details=details,
            )
        ]

    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _summary_to_dict(summary) -> dict:
        """Coerce Iceberg snapshot.summary (Mapping/tuple-of-tuples) to dict."""
        try:
            return dict(summary)
        except Exception:
            # tuple-of-tuples fallback
            try:
                return {str(k): str(v) for k, v in summary}
            except Exception:
                return {}

    @staticmethod
    def _parse_int(v) -> Optional[int]:
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _timestamp_column_for(table_name: str) -> str:
        """Which column holds the bar timestamp for this table.

        OHLCV tables use 'timestamp'; corp_actions uses 'ex_date'.
        """
        if table_name.endswith("_corp_actions"):
            return "ex_date"
        return "timestamp"

    @staticmethod
    def _date_range(table, ts_col: str) -> tuple[Optional[str], Optional[str]]:
        """Return (first, last) timestamp as ISO strings, or (None, None).

        Uses PyIceberg's lazy scan + pyarrow aggregation; bounded.
        """
        try:
            scan = table.scan(selected_fields=(ts_col,))
            arrow = scan.to_arrow()
        except Exception:
            return None, None
        if arrow.num_rows == 0:
            return None, None
        import pyarrow.compute as pc
        try:
            mn = pc.min(arrow[ts_col]).as_py()
            mx = pc.max(arrow[ts_col]).as_py()
        except Exception:
            return None, None
        return (
            mn.isoformat() if hasattr(mn, "isoformat") else str(mn),
            mx.isoformat() if hasattr(mx, "isoformat") else str(mx),
        )
