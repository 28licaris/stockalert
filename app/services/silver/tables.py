"""
Idempotent Iceberg table creation for the silver layer.

Mirrors `app/services/bronze/tables.py`. Every startup or import job
can call these to ensure the right silver table exists with the right
schema/partition/sort spec. PyIceberg raises `TableAlreadyExists` if
you call `create_table` on an existing table, so we check first.
"""
from __future__ import annotations

import logging

from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import (
    NamespaceAlreadyExistsError,
    NoSuchNamespaceError,
    NoSuchTableError,
)
from pyiceberg.table import Table

from app.config import settings
from app.services.iceberg_catalog import get_catalog
from app.services.silver.schemas import (
    SILVER_CORP_ACTIONS_PARTITION,
    SILVER_CORP_ACTIONS_SCHEMA,
    SILVER_CORP_ACTIONS_SORT,
    silver_table_id,
)

log = logging.getLogger(__name__)


def _ensure_namespace(catalog: Catalog) -> None:
    """Glue database (e.g. `stock_lake`) must exist before tables go in it.

    Same as the bronze-side helper; lifted here so silver doesn't
    depend on internal bronze module structure.
    """
    db = settings.iceberg_glue_database
    try:
        catalog.list_namespaces(db)
    except NoSuchNamespaceError:
        try:
            catalog.create_namespace(db)
        except NamespaceAlreadyExistsError:
            pass


def ensure_silver_corp_actions(catalog: Catalog | None = None) -> Table:
    """
    Create `silver.corp_actions` if it doesn't exist; return the table.

    Glue databases are flat — there's no real `silver.` namespace.
    The medallion separation is purely on-disk (S3 `iceberg/silver/`
    prefix) and via table-name prefix. The fully-qualified catalog
    identifier is `stock_lake.corp_actions`.

    Idempotent: safe to call from app startup, from ingest jobs, from
    operator scripts. Returns the existing table if present.
    """
    catalog = catalog or get_catalog()
    _ensure_namespace(catalog)

    table_id = silver_table_id("corp_actions")
    try:
        return catalog.load_table(table_id)
    except NoSuchTableError:
        pass

    warehouse = (
        f"s3://{settings.stock_lake_bucket}/{settings.iceberg_warehouse_prefix}"
    )
    location = f"{warehouse}/silver/corp_actions"

    log.info("Creating silver table %s at %s", table_id, location)
    return catalog.create_table(
        identifier=table_id,
        schema=SILVER_CORP_ACTIONS_SCHEMA,
        location=location,
        partition_spec=SILVER_CORP_ACTIONS_PARTITION,
        sort_order=SILVER_CORP_ACTIONS_SORT,
        properties={
            # Corp-actions are sparse — small files are fine. Target a
            # smaller file size than bronze (256 MB) since per-year
            # partitions for the full universe might be only a few MB.
            "write.target-file-size-bytes": str(64 * 1024 * 1024),
            "write.parquet.compression-codec": "snappy",
        },
    )
