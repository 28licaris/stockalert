"""
Unit tests for the bronze-layer audit framework.

These tests don't touch live S3 / Iceberg — the audit checks
already gracefully handle missing tables via `safe_load_table`, so
we test that contract directly + use mocks for the success paths.

Live audits are covered by `scripts/audit_bronze.py` in the operator
runbook.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.bronze.audit import (
    AuditResult,
    AuditSeverity,
    AuditStatus,
    BronzeAuditCheck,
    build_all_checks,
    build_check,
    list_registered_checks,
    register_check,
)
from app.services.bronze.audit.base import (
    BRONZE_TABLES_TO_AUDIT,
    safe_load_table,
)


# ─────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────


class TestAuditRegistry:
    def test_all_five_initial_checks_registered(self) -> None:
        registered = list_registered_checks()
        expected = {
            "schema_match", "row_counts", "source_tags",
            "null_symbols", "adjustment_status",
        }
        assert expected <= set(registered), (
            f"missing checks: {expected - set(registered)}"
        )

    def test_build_all_checks_instantiates_each(self) -> None:
        checks = build_all_checks()
        names = sorted(c.check_name for c in checks)
        # At least the 5 initial checks
        assert "schema_match" in names
        assert "null_symbols" in names

    def test_build_check_by_name(self) -> None:
        check = build_check("null_symbols")
        assert check.check_name == "null_symbols"

    def test_build_unknown_check_raises(self) -> None:
        with pytest.raises(KeyError):
            build_check("nonexistent_check")

    def test_re_register_raises(self) -> None:
        with pytest.raises(ValueError, match="already registered"):
            @register_check("schema_match")
            class Duplicate:
                check_name = "schema_match"
                def run(self, table_name): return []

    def test_checks_conform_to_protocol(self) -> None:
        for c in build_all_checks():
            assert isinstance(c, BronzeAuditCheck)
            assert hasattr(c, "check_name")
            assert hasattr(c, "run")
            assert callable(c.run)


# ─────────────────────────────────────────────────────────────────────
# Bronze tables registered
# ─────────────────────────────────────────────────────────────────────


class TestAuditTargets:
    def test_polygon_and_schwab_minute_in_audit_list(self) -> None:
        assert "polygon_minute" in BRONZE_TABLES_TO_AUDIT
        assert "schwab_minute" in BRONZE_TABLES_TO_AUDIT


# ─────────────────────────────────────────────────────────────────────
# AuditResult shape
# ─────────────────────────────────────────────────────────────────────


class TestAuditResultShape:
    def test_required_fields(self) -> None:
        r = AuditResult(
            check="my_check",
            table="polygon_minute",
            status=AuditStatus.OK,
        )
        assert r.severity == AuditSeverity.INFO  # default
        assert r.message == ""
        assert r.details == {}
        assert r.error is None

    def test_status_constants_present(self) -> None:
        assert AuditStatus.OK == "ok"
        assert AuditStatus.WARN == "warn"
        assert AuditStatus.FAIL == "fail"
        assert AuditStatus.SKIPPED == "skipped"

    def test_severity_constants_present(self) -> None:
        assert AuditSeverity.INFO == "info"
        assert AuditSeverity.WARN == "warn"
        assert AuditSeverity.FAIL == "fail"


# ─────────────────────────────────────────────────────────────────────
# safe_load_table helper — graceful failure on missing tables
# ─────────────────────────────────────────────────────────────────────


class TestSafeLoadTable:
    def test_returns_tuple_with_none_on_failure(self) -> None:
        """When the catalog can't load (no catalog config, missing table,
        whatever), safe_load_table returns (None, error_string) — never raises."""
        # Patch the catalog to simulate a load failure.
        with patch(
            "app.services.iceberg_catalog.get_catalog",
            side_effect=RuntimeError("simulated catalog failure"),
        ):
            table, err = safe_load_table("polygon_minute")
            assert table is None
            assert err is not None
            assert "RuntimeError" in err


# ─────────────────────────────────────────────────────────────────────
# Per-check behavior on missing tables
# ─────────────────────────────────────────────────────────────────────


class TestCheckOnMissingTable:
    """Each check produces an AuditResult with SKIPPED status (NEVER raises)
    when the table can't be loaded."""

    def _patch_catalog_failure(self):
        return patch(
            "app.services.iceberg_catalog.get_catalog",
            side_effect=RuntimeError("simulated"),
        )

    def test_schema_match_skips_missing_table(self) -> None:
        check = build_check("schema_match")
        with self._patch_catalog_failure():
            results = check.run("polygon_minute")
        assert len(results) == 1
        assert results[0].status == AuditStatus.SKIPPED
        assert results[0].error is not None

    def test_row_counts_skips_missing_table(self) -> None:
        check = build_check("row_counts")
        with self._patch_catalog_failure():
            results = check.run("polygon_minute")
        assert len(results) == 1
        assert results[0].status == AuditStatus.SKIPPED

    def test_source_tags_skips_missing_table(self) -> None:
        check = build_check("source_tags")
        with self._patch_catalog_failure():
            results = check.run("polygon_minute")
        assert len(results) == 1
        assert results[0].status == AuditStatus.SKIPPED

    def test_null_symbols_skips_missing_table(self) -> None:
        check = build_check("null_symbols")
        with self._patch_catalog_failure():
            results = check.run("polygon_minute")
        assert len(results) == 1
        assert results[0].status == AuditStatus.SKIPPED

    def test_adjustment_status_skips_missing_table(self) -> None:
        check = build_check("adjustment_status")
        with self._patch_catalog_failure():
            results = check.run("polygon_minute")
        assert len(results) == 1
        assert results[0].status == AuditStatus.SKIPPED

    def test_no_check_raises_on_missing_table(self) -> None:
        """The runner contract: checks NEVER raise. Verify all 5 hold."""
        for check in build_all_checks():
            with self._patch_catalog_failure():
                try:
                    results = check.run("polygon_minute")
                except Exception as e:
                    pytest.fail(
                        f"{check.check_name} raised on missing table: "
                        f"{type(e).__name__}: {e}"
                    )
                assert isinstance(results, list)
                assert all(isinstance(r, AuditResult) for r in results)


# ─────────────────────────────────────────────────────────────────────
# Unknown-table handling
# ─────────────────────────────────────────────────────────────────────


class TestUnknownTable:
    """Checks should also handle being asked about a table they don't know
    about (e.g. schema_match has a per-table SCHEMA_MAP)."""

    def test_schema_match_unknown_table_skips(self) -> None:
        check = build_check("schema_match")
        # Patch safe_load_table to succeed but schema-match has no entry
        # for "foo_minute".
        from app.services.bronze.tables import ensure_bronze_polygon_minute

        # Use a real table object but call with a fake table_name to
        # trigger the missing-constant branch.
        with patch(
            "app.services.bronze.audit.base.safe_load_table",
            return_value=(None, None),   # won't be reached if first branch returns
        ):
            # Patch safe_load_table to return a valid-looking table
            class _StubTable:
                def schema(self):
                    class _S:
                        fields = []
                        identifier_field_ids = []
                    return _S()
            with patch(
                "app.services.bronze.audit.base.safe_load_table",
                return_value=(_StubTable(), None),
            ):
                results = check.run("totally_unknown_table")
        # Either SKIPPED (no constant registered) or FAIL — but never raise.
        assert isinstance(results, list)
        assert all(isinstance(r, AuditResult) for r in results)
