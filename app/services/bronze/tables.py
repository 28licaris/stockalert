"""
Idempotent Iceberg table creation for the bronze layer.

Mirrors the role `app/db/init.py` plays for ClickHouse: every startup
or import job can call these to ensure the right table exists with the
right schema/partition/sort spec. PyIceberg raises `TableAlreadyExists`
if you call `create_table` on an existing table, so we check first.
"""
from __future__ import annotations

import logging

from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchNamespaceError, NoSuchTableError
from pyiceberg.table import Table

from app.services.bronze.schemas import (
    BRONZE_POLYGON_MINUTE_PARTITION,
    BRONZE_POLYGON_MINUTE_SCHEMA,
    BRONZE_POLYGON_MINUTE_SORT,
    BRONZE_SCHWAB_MINUTE_PARTITION,
    BRONZE_SCHWAB_MINUTE_SCHEMA,
    BRONZE_SCHWAB_MINUTE_SORT,
    bronze_table_id,
)
from app.services.iceberg_catalog import get_catalog
from app.config import settings

log = logging.getLogger(__name__)


def _ensure_namespace(catalog: Catalog) -> None:
    db = settings.iceberg_glue_database
    try:
        catalog.list_namespaces(db)
    except NoSuchNamespaceError:
        try:
            catalog.create_namespace(db)
        except NamespaceAlreadyExistsError:
            pass


def ensure_bronze_polygon_minute(catalog: Catalog | None = None) -> Table:
    """
    Create `bronze.polygon_minute` if it doesn't exist; return the table.

    Iceberg "tables" in a Glue database don't actually use a `bronze.`
    namespace — Glue databases are flat. We mimic the bronze/silver/gold
    layering via table-name prefix (`polygon_minute`, `polygon_day`, etc.)
    inside one Glue database, which keeps catalog navigation simple.
    Table location goes under `s3://bucket/iceberg/bronze/polygon_minute/`
    so the on-disk layout still reflects the medallion.
    """
    catalog = catalog or get_catalog()
    _ensure_namespace(catalog)

    table_id = bronze_table_id("polygon_minute")
    try:
        return catalog.load_table(table_id)
    except NoSuchTableError:
        pass

    warehouse = f"s3://{settings.stock_lake_bucket}/{settings.iceberg_warehouse_prefix}"
    location = f"{warehouse}/bronze/polygon_minute"

    log.info("Creating bronze table %s at %s", table_id, location)
    return catalog.create_table(
        identifier=table_id,
        schema=BRONZE_POLYGON_MINUTE_SCHEMA,
        location=location,
        partition_spec=BRONZE_POLYGON_MINUTE_PARTITION,
        sort_order=BRONZE_POLYGON_MINUTE_SORT,
        properties={
            # 256 MB target — bigger than the conservative 128 MB default,
            # smaller than the 512 MB Trino-ish recommendation. Balances
            # parallelism (more files = more readers can work) against
            # S3 per-file open overhead.
            "write.target-file-size-bytes": str(256 * 1024 * 1024),
            "write.parquet.compression-codec": "snappy",
        },
    )


def ensure_bronze_schwab_minute(catalog: Catalog | None = None) -> Table:
    """
    Create `bronze.schwab_minute` if absent; return the table.

    Same shape and conventions as `bronze.polygon_minute` — see that
    function's docstring. Schwab's pricehistory REST is per-symbol
    rather than whole-market, so daily writes are many small appends
    (one per symbol). Monthly compaction collapses them.
    """
    catalog = catalog or get_catalog()
    _ensure_namespace(catalog)

    table_id = bronze_table_id("schwab_minute")
    try:
        return catalog.load_table(table_id)
    except NoSuchTableError:
        pass

    warehouse = f"s3://{settings.stock_lake_bucket}/{settings.iceberg_warehouse_prefix}"
    location = f"{warehouse}/bronze/schwab_minute"

    log.info("Creating bronze table %s at %s", table_id, location)
    return catalog.create_table(
        identifier=table_id,
        schema=BRONZE_SCHWAB_MINUTE_SCHEMA,
        location=location,
        partition_spec=BRONZE_SCHWAB_MINUTE_PARTITION,
        sort_order=BRONZE_SCHWAB_MINUTE_SORT,
        properties={
            "write.target-file-size-bytes": str(256 * 1024 * 1024),
            "write.parquet.compression-codec": "snappy",
        },
    )
