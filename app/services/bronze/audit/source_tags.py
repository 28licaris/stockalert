"""
Source-tag distribution audit.

Bronze tables tag every row with a `source` string (provider + path):
  - `polygon-flatfiles`: from the nightly flat-file import
  - `polygon-rest`:      from REST backfills
  - `polygon`:           from live WebSocket stream
  - `schwab`:            from REST pricehistory backfills
  - `schwab-stream`:     from CHART_EQUITY live stream

Bronze README documents the expected set per provider. This check
verifies the actual distinct values match. Unexpected tags can mean:
  - a new ingest path was wired without updating docs
  - a typo in a tag literal (`polygon_rest` vs `polygon-rest`)
  - silent provider tag drift on an API revision

Each unexpected tag becomes an AuditResult so the operator gets the
full list, not just a count.
"""
from __future__ import annotations

from app.services.bronze.audit import register_check
from app.services.bronze.audit.base import (
    AuditResult,
    AuditSeverity,
    AuditStatus,
    safe_load_table,
)


# Per-table expected set of source-tag values. Add a new tag here
# when an intentional new ingest path lands.
_EXPECTED_TAGS: dict[str, set[str]] = {
    "polygon_minute": {"polygon-flatfiles", "polygon-rest", "polygon"},
    "schwab_minute": {"schwab", "schwab-stream"},
    "polygon_corp_actions": {"polygon"},
}


@register_check("source_tags")
class SourceTagsCheck:
    """Verify distinct `source` values per bronze table match the
    documented set."""

    check_name = "source_tags"

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

        # corp_actions has a `source_provider` column, not `source`.
        source_col = (
            "source_provider"
            if table_name.endswith("_corp_actions") else "source"
        )

        try:
            scan = table.scan(selected_fields=(source_col,))
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
                    message="empty table; no source tags to check",
                )
            ]

        # Distinct values + counts
        import pyarrow.compute as pc
        col = arrow[source_col]
        try:
            counts = pc.value_counts(col).to_pylist()
        except Exception as e:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.SKIPPED,
                    message=f"value_counts failed: {type(e).__name__}",
                    error=str(e),
                )
            ]

        # value_counts returns [{"values": <value>, "counts": <int>}, ...]
        # but in pyarrow >= 14 it returns ListOfStructs flattened differently.
        actual_counts: dict[str, int] = {}
        for entry in counts:
            v = entry.get("values")
            c = entry.get("counts")
            if v is not None:
                actual_counts[str(v)] = int(c or 0)

        expected = _EXPECTED_TAGS.get(table_name, set())
        actual_set = set(actual_counts)
        unexpected = actual_set - expected
        missing = expected - actual_set
        null_count = actual_counts.get("None", 0)
        if null_count == 0:
            # also try sentinel for None passed through as string
            null_count = sum(c for v, c in actual_counts.items() if v in ("", "null"))

        results: list[AuditResult] = []

        # Status logic:
        # - UNEXPECTED tags = WARN (something undocumented in the lake;
        #   could be a typo or undocumented ingest path).
        # - MISSING expected tags = INFO only (an expected ingest path is
        #   not currently active — e.g. live-stream not running, REST
        #   backfill not invoked yet). NOT a failure.
        # - NULL source = WARN (rows that should have a tag don't).
        if unexpected:
            status = AuditStatus.WARN
            severity = AuditSeverity.WARN
            message = (
                f"{len(unexpected)} UNEXPECTED source tag(s): {sorted(unexpected)}"
            )
        elif null_count > 0:
            status = AuditStatus.WARN
            severity = AuditSeverity.WARN
            message = f"{null_count:,} rows with null/empty source tag"
        else:
            status = AuditStatus.OK
            severity = AuditSeverity.INFO
            message = f"{len(actual_set)} distinct source tag(s): {sorted(actual_set)}"
            if missing:
                message += f"  (expected-but-absent: {sorted(missing)} — ingest path not active)"

        results.append(
            AuditResult(
                check=self.check_name,
                table=table_name,
                status=status,
                severity=severity,
                message=message,
                details={
                    "counts": actual_counts,
                    "expected": sorted(expected),
                    "unexpected": sorted(unexpected),
                    "missing_expected_but_absent": sorted(missing),
                    "null_source_count": null_count,
                },
            )
        )

        return results
