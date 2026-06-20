"""Idempotent creation of the `elliott_wave_labels` Iceberg table.

Operator/test-callable (mirrors app/services/equities/tables.py). Creating the
table is a deliberate op — it is NOT auto-run on import. Works for both the
`equities` and `futures` namespaces via `asset_class`.
"""
from __future__ import annotations

import logging

from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.table import Table

from app.services.elliott_store.schema import (
    ELLIOTT_WAVE_LABELS_PARTITION,
    ELLIOTT_WAVE_LABELS_SCHEMA,
    ELLIOTT_WAVE_LABELS_SORT,
    glue_database,
    label_table_id,
    label_table_location,
)
from app.services.iceberg_catalog import get_catalog

log = logging.getLogger(__name__)

_PROPERTIES: dict[str, str] = {
    "format-version": "2",
    "write.parquet.compression-codec": "zstd",
    "write.distribution-mode": "hash",
    "write.target-file-size-bytes": str(64 * 1024 * 1024),
    "write.parquet.row-group-size-bytes": str(16 * 1024 * 1024),
}


def _ensure_namespace(catalog: Catalog, asset_class: str) -> None:
    try:
        catalog.create_namespace(glue_database(asset_class))
    except NamespaceAlreadyExistsError:
        pass


def _evolve(table: Table) -> None:
    """Idempotent forward schema evolution for tables created before a column
    was added (e.g. `as_of_price`, EW-6). No-op once the column exists."""
    from pyiceberg.types import DoubleType, StringType

    have = set(table.schema().column_names)
    with table.update_schema() as upd:
        if "as_of_price" not in have:
            upd.add_column("as_of_price", DoubleType(), required=False)
        if "p_nesting_score" not in have:        # V3-1
            upd.add_column("p_nesting_score", DoubleType(), required=False)
        if "p_forward" not in have:              # V3-2 (JSON)
            upd.add_column("p_forward", StringType(), required=False)


def ensure_elliott_wave_labels(asset_class: str = "equity",
                               catalog: Catalog | None = None) -> Table:
    """Create `<ns>.elliott_wave_labels` if absent; return the table."""
    catalog = catalog or get_catalog()
    _ensure_namespace(catalog, asset_class)

    table_id = label_table_id(asset_class)
    try:
        table = catalog.load_table(table_id)
        _evolve(table)
        return table
    except NoSuchTableError:
        pass

    location = label_table_location(asset_class)
    log.info("Creating Iceberg table %s at %s", table_id, location)
    return catalog.create_table(
        identifier=table_id,
        schema=ELLIOTT_WAVE_LABELS_SCHEMA,
        location=location,
        partition_spec=ELLIOTT_WAVE_LABELS_PARTITION,
        sort_order=ELLIOTT_WAVE_LABELS_SORT,
        properties=_PROPERTIES,
    )
