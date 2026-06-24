"""Unit tests for `app/services/equities/tables.py` (CV1).

Mocks the PyIceberg `Catalog` interface to verify idempotent
ensure_*() behavior without touching AWS Glue. Pairs with the
integration test (gated by AWS creds) that exercises a live catalog.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pyiceberg = pytest.importorskip("pyiceberg")

from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError  # noqa: E402

from app.services.equities import tables as equities_tables  # noqa: E402


def _make_catalog(*, table_exists: bool, namespace_exists: bool = True):
    """Build a MagicMock Catalog with the requested load behavior.

    `_ensure_namespace` always calls `create_namespace` and swallows
    `NamespaceAlreadyExistsError` — so we mock that, not `list_namespaces`.
    """
    catalog = MagicMock()

    if namespace_exists:
        catalog.create_namespace.side_effect = NamespaceAlreadyExistsError("exists")
    # else: create_namespace returns a MagicMock (succeeds silently).

    if table_exists:
        existing = MagicMock(name="ExistingTable")
        catalog.load_table.return_value = existing
        catalog.create_table.side_effect = AssertionError(
            "create_table must not be called when load_table succeeds"
        )
    else:
        catalog.load_table.side_effect = NoSuchTableError("missing")
        catalog.create_table.return_value = MagicMock(name="CreatedTable")

    return catalog


def test_ensure_polygon_raw_loads_existing_table_without_creating():
    catalog = _make_catalog(table_exists=True)
    result = equities_tables.ensure_polygon_raw(catalog)

    catalog.load_table.assert_called_once_with("equities.polygon_raw")
    catalog.create_table.assert_not_called()
    assert result is catalog.load_table.return_value


def test_ensure_polygon_raw_creates_when_missing():
    catalog = _make_catalog(table_exists=False)
    result = equities_tables.ensure_polygon_raw(catalog)

    catalog.load_table.assert_called_once_with("equities.polygon_raw")
    catalog.create_table.assert_called_once()
    kwargs = catalog.create_table.call_args.kwargs
    assert kwargs["identifier"] == "equities.polygon_raw"
    assert kwargs["schema"] is equities_tables.POLYGON_RAW_SCHEMA
    assert kwargs["partition_spec"] is equities_tables.POLYGON_RAW_PARTITION
    assert kwargs["sort_order"] is equities_tables.POLYGON_RAW_SORT
    # 128 MB target file size per Gate 4 / 02_schema.md DDL.
    assert kwargs["properties"]["write.target-file-size-bytes"] == str(128 * 1024 * 1024)
    assert kwargs["properties"]["write.parquet.compression-codec"] == "zstd"
    assert kwargs["properties"]["format-version"] == "2"
    # Raw bars don't need merge-on-read.
    assert "write.merge.mode" not in kwargs["properties"]
    assert result is catalog.create_table.return_value


def test_ensure_polygon_adjusted_sets_merge_on_read():
    catalog = _make_catalog(table_exists=False)
    equities_tables.ensure_polygon_adjusted(catalog)

    props = catalog.create_table.call_args.kwargs["properties"]
    assert props["write.merge.mode"] == "merge-on-read"
    assert props["write.update.mode"] == "merge-on-read"
    assert props["write.delete.mode"] == "merge-on-read"


def test_ensure_schwab_universe_sets_merge_on_read():
    catalog = _make_catalog(table_exists=False)
    equities_tables.ensure_schwab_universe(catalog)

    props = catalog.create_table.call_args.kwargs["properties"]
    assert props["write.merge.mode"] == "merge-on-read"


def test_ensure_market_corp_actions_uses_smaller_target_file_size():
    catalog = _make_catalog(table_exists=False)
    equities_tables.ensure_market_corp_actions(catalog)

    props = catalog.create_table.call_args.kwargs["properties"]
    # 64 MB target — corp_actions is a sparse, low-row-count table.
    assert props["write.target-file-size-bytes"] == str(64 * 1024 * 1024)
    assert "write.merge.mode" not in props, "no MoR on append-mostly table"


def test_ensure_creates_namespace_when_missing():
    catalog = _make_catalog(table_exists=False, namespace_exists=False)
    equities_tables.ensure_polygon_raw(catalog)

    catalog.create_namespace.assert_called_once_with("equities")


def test_ensure_swallows_namespace_already_exists():
    """Idempotent create: attempt every time, swallow AlreadyExists.

    Regression guard for the bug where `list_namespaces(db)` returned
    `[]` on a missing namespace (no exception), so the old probe
    silently no-op'd and `create_table` then failed with
    `Database not found`.
    """
    catalog = _make_catalog(table_exists=False, namespace_exists=True)
    equities_tables.ensure_polygon_raw(catalog)

    catalog.create_namespace.assert_called_once_with("equities")


def test_ensure_all_creates_four_tables(monkeypatch):
    catalog = _make_catalog(table_exists=False)
    result = equities_tables.ensure_all(catalog)

    assert set(result.keys()) == {
        "polygon_raw",
        "polygon_adjusted",
        "schwab_universe",
        "market_corp_actions",
    }
    assert catalog.create_table.call_count == 4


def test_ensure_equities_table_dispatches_known_name():
    """ensure_equities_table('schwab_universe') must delegate to
    ensure_schwab_universe — the live writer relies on this dispatch
    to avoid NoSuchTableError on cold-start."""
    catalog = _make_catalog(table_exists=False)
    result = equities_tables.ensure_equities_table("schwab_universe", catalog)

    catalog.create_table.assert_called_once()
    kwargs = catalog.create_table.call_args.kwargs
    assert kwargs["identifier"] == "equities.schwab_universe"
    assert result is catalog.create_table.return_value


def test_ensure_equities_table_raises_on_unknown_name():
    """Unknown short_name must raise ValueError, NOT silently create
    a bogus table (NO_SILENT_FAILURES)."""
    catalog = _make_catalog(table_exists=False)

    with pytest.raises(ValueError, match="Unknown equities table"):
        equities_tables.ensure_equities_table("nonexistent_table", catalog)

    catalog.create_table.assert_not_called()


def test_ensure_equities_table_covers_all_four_v2_tables():
    """Dispatcher must cover every v2 table — a stale dispatcher would
    silently fail any caller using a newly-added table name."""
    for short_name in (
        "polygon_raw",
        "polygon_adjusted",
        "schwab_universe",
        "market_corp_actions",
    ):
        catalog = _make_catalog(table_exists=False)
        equities_tables.ensure_equities_table(short_name, catalog)
        kwargs = catalog.create_table.call_args.kwargs
        assert kwargs["identifier"] == f"equities.{short_name}"


def test_table_locations_match_warehouse_layout(monkeypatch):
    """Locations must land at s3://{bucket}/{prefix}/equities/{table}/
    per 03_s3_layout.md (post-CV1 patch). A regression here means data
    files would go to the wrong S3 path and disappear from queries."""
    from app.config import settings

    monkeypatch.setattr(settings, "stock_lake_bucket", "test-bucket")
    monkeypatch.setattr(settings, "iceberg_warehouse_prefix", "iceberg")
    monkeypatch.setattr(settings, "iceberg_equities_glue_database", "equities")

    catalog = _make_catalog(table_exists=False)
    equities_tables.ensure_polygon_raw(catalog)
    assert catalog.create_table.call_args.kwargs["location"] == (
        "s3://test-bucket/iceberg/equities/polygon_raw"
    )
