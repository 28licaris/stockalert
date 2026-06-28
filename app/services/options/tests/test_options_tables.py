from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("pyiceberg")

from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError  # noqa: E402

from app.services.options import tables as options_tables  # noqa: E402


def _catalog(*, table_exists: bool, namespace_exists: bool = True) -> MagicMock:
    catalog = MagicMock()
    if namespace_exists:
        catalog.create_namespace.side_effect = NamespaceAlreadyExistsError("exists")
    if table_exists:
        existing = MagicMock(name="ExistingTable")
        catalog.load_table.return_value = existing
        catalog.create_table.side_effect = AssertionError("create_table must not be called")
    else:
        catalog.load_table.side_effect = NoSuchTableError("missing")
        catalog.create_table.return_value = MagicMock(name="CreatedTable")
    return catalog


def test_options_table_id_uses_options_namespace(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "iceberg_options_glue_database", "options_test")

    assert options_tables.options_table_id("schwab_chain_raw") == "options_test.schwab_chain_raw"


def test_ensure_chain_raw_loads_existing_table() -> None:
    catalog = _catalog(table_exists=True)
    result = options_tables.ensure_chain_raw(catalog)

    catalog.load_table.assert_called_once_with("options.schwab_chain_raw")
    catalog.create_table.assert_not_called()
    assert result is catalog.load_table.return_value


def test_ensure_chain_contracts_creates_with_expected_schema() -> None:
    catalog = _catalog(table_exists=False)
    result = options_tables.ensure_chain_contracts(catalog)

    kwargs = catalog.create_table.call_args.kwargs
    assert kwargs["identifier"] == "options.schwab_chain_contracts"
    assert kwargs["schema"] is options_tables.CHAIN_CONTRACTS_SCHEMA
    assert kwargs["partition_spec"] is options_tables.CHAIN_CONTRACTS_PARTITION
    assert kwargs["sort_order"] is options_tables.CHAIN_CONTRACTS_SORT
    assert kwargs["properties"]["format-version"] == "2"
    assert kwargs["properties"]["write.merge.mode"] == "merge-on-read"
    assert result is catalog.create_table.return_value


def test_ensure_all_creates_all_options_tables() -> None:
    catalog = _catalog(table_exists=False)
    result = options_tables.ensure_all(catalog)

    assert set(result) == {
        "schwab_chain_raw",
        "schwab_chain_contracts",
        "schwab_expirations",
        "gamma_exposure_snapshots",
    }
    assert catalog.create_table.call_count == 4


def test_ensure_options_table_dispatches_and_rejects_unknown() -> None:
    catalog = _catalog(table_exists=False)
    options_tables.ensure_options_table("gamma_exposure_snapshots", catalog)

    kwargs = catalog.create_table.call_args.kwargs
    assert kwargs["identifier"] == "options.gamma_exposure_snapshots"

    with pytest.raises(ValueError, match="Unknown options table"):
        options_tables.ensure_options_table("missing", catalog)


def test_options_table_location_matches_warehouse_layout(monkeypatch) -> None:
    from app.config import settings

    monkeypatch.setattr(settings, "stock_lake_bucket", "test-bucket")
    monkeypatch.setattr(settings, "iceberg_warehouse_prefix", "iceberg")
    monkeypatch.setattr(settings, "iceberg_options_glue_database", "options")

    catalog = _catalog(table_exists=False)
    options_tables.ensure_chain_raw(catalog)

    assert catalog.create_table.call_args.kwargs["location"] == (
        "s3://test-bucket/iceberg/options/schwab_chain_raw"
    )


def test_options_schema_contains_expected_contract_identifiers() -> None:
    schema = options_tables.CHAIN_CONTRACTS_SCHEMA
    fields = {field.name: field.field_id for field in schema.fields}

    assert fields["underlying_symbol"] in schema.identifier_field_ids
    assert fields["option_symbol"] in schema.identifier_field_ids
    assert fields["snapshot_ts"] in schema.identifier_field_ids


def test_gamma_schema_uses_required_level_key_identifier() -> None:
    schema = options_tables.GAMMA_EXPOSURE_SCHEMA
    fields = {field.name: field.field_id for field in schema.fields}

    assert fields["underlying_symbol"] in schema.identifier_field_ids
    assert fields["snapshot_ts"] in schema.identifier_field_ids
    assert fields["aggregation_level"] in schema.identifier_field_ids
    assert fields["level_key"] in schema.identifier_field_ids
