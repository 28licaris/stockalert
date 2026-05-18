"""
Schema-match audit: on-disk Iceberg schema == declared schema in `bronze/schemas.py`.

A schema drift here means our code thinks bronze has columns X, Y, Z
but the actual lake has X, Y, W. Every downstream consumer would
silently break (or worse, silently work on wrong columns).

Compares the table's loaded schema against the corresponding
`BRONZE_*_MINUTE_SCHEMA` declaration field-by-field:
- field count
- field IDs
- field names
- field types
- required flag
- identifier_field_ids
"""
from __future__ import annotations

from app.services.bronze.audit import register_check
from app.services.bronze.audit.base import (
    AuditResult,
    AuditSeverity,
    AuditStatus,
    safe_load_table,
)


@register_check("schema_match")
class SchemaMatchCheck:
    """Verify the on-disk Iceberg schema matches the declared schema."""

    check_name = "schema_match"

    # Map bronze short-name → declared Schema constant in bronze/schemas.py
    _DECLARED_SCHEMAS = {
        "polygon_minute": "BRONZE_POLYGON_MINUTE_SCHEMA",
        "schwab_minute": "BRONZE_SCHWAB_MINUTE_SCHEMA",
        "polygon_corp_actions": "BRONZE_POLYGON_CORP_ACTIONS_SCHEMA",
    }

    def run(self, table_name: str) -> list[AuditResult]:
        table, err = safe_load_table(table_name)
        if err:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.SKIPPED,
                    severity=AuditSeverity.INFO,
                    message="cannot load table",
                    error=err,
                )
            ]

        # Lookup the declared schema by name from bronze.schemas
        from app.services.bronze import schemas as bronze_schemas

        declared_const = self._DECLARED_SCHEMAS.get(table_name)
        if declared_const is None:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.SKIPPED,
                    severity=AuditSeverity.INFO,
                    message=f"no declared schema constant registered for {table_name}",
                )
            ]
        declared = getattr(bronze_schemas, declared_const, None)
        if declared is None:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.FAIL,
                    severity=AuditSeverity.FAIL,
                    message=f"declared schema constant {declared_const} not found in bronze.schemas",
                )
            ]

        actual = table.schema()

        diffs: list[str] = []

        # Field count
        if len(actual.fields) != len(declared.fields):
            diffs.append(
                f"field count: actual={len(actual.fields)}, "
                f"declared={len(declared.fields)}"
            )

        # Identifier fields
        if tuple(sorted(actual.identifier_field_ids or [])) != tuple(
            sorted(declared.identifier_field_ids or [])
        ):
            diffs.append(
                f"identifier_field_ids: actual={actual.identifier_field_ids}, "
                f"declared={declared.identifier_field_ids}"
            )

        # Per-field comparison by field-id
        actual_by_id = {f.field_id: f for f in actual.fields}
        declared_by_id = {f.field_id: f for f in declared.fields}
        all_ids = set(actual_by_id) | set(declared_by_id)
        for fid in sorted(all_ids):
            a = actual_by_id.get(fid)
            d = declared_by_id.get(fid)
            if a is None:
                diffs.append(f"field_id={fid}: declared {d.name} missing on disk")
                continue
            if d is None:
                diffs.append(f"field_id={fid}: on-disk {a.name} not declared")
                continue
            if a.name != d.name:
                diffs.append(
                    f"field_id={fid}: name mismatch actual={a.name!r} "
                    f"declared={d.name!r}"
                )
            if str(a.field_type) != str(d.field_type):
                diffs.append(
                    f"field_id={fid} ({a.name}): type mismatch actual="
                    f"{a.field_type} declared={d.field_type}"
                )
            if bool(a.required) != bool(d.required):
                diffs.append(
                    f"field_id={fid} ({a.name}): required mismatch "
                    f"actual={a.required} declared={d.required}"
                )

        if diffs:
            return [
                AuditResult(
                    check=self.check_name,
                    table=table_name,
                    status=AuditStatus.FAIL,
                    severity=AuditSeverity.FAIL,
                    message=f"{len(diffs)} schema drift(s) detected",
                    details={"diffs": diffs},
                )
            ]

        return [
            AuditResult(
                check=self.check_name,
                table=table_name,
                status=AuditStatus.OK,
                severity=AuditSeverity.INFO,
                message=f"{len(actual.fields)} fields match declaration",
                details={
                    "field_count": len(actual.fields),
                    "identifier_field_ids": list(actual.identifier_field_ids or []),
                },
            )
        ]
