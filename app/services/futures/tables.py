"""Create the futures Glue DB + Iceberg table (idempotent).

Mirrors `app/services/equities/tables.py` but for the `futures` Glue DB
and `iceberg/futures/` S3 location. One table: `futures.schwab_futures`.
"""
from __future__ import annotations

import logging

from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.table import Table

from app.config import settings
from app.services.equities.tables import _BASE_PROPERTIES, _MOR_PROPERTIES
from app.services.futures.schemas import (
    FUTURES_OHLCV_PARTITION,
    FUTURES_OHLCV_SCHEMA,
    FUTURES_OHLCV_SORT,
    futures_table_id,
)
from app.services.iceberg_catalog import get_catalog

log = logging.getLogger(__name__)

FUTURES_TABLE_NAME = "schwab_futures"


def _ensure_namespace(catalog: Catalog) -> None:
    """Create the `futures` Glue database if absent (always-attempt; see
    equities.tables._ensure_namespace for the Glue list-vs-create gotcha)."""
    db = settings.iceberg_futures_glue_database
    try:
        catalog.create_namespace(db)
        log.info("Created Iceberg namespace %s", db)
    except NamespaceAlreadyExistsError:
        pass


def _futures_table_location(table_name: str) -> str:
    """s3://{bucket}/{warehouse_prefix}/{futures_db}/{table_name}/"""
    return (
        f"s3://{settings.stock_lake_bucket}/{settings.iceberg_warehouse_prefix}/"
        f"{settings.iceberg_futures_glue_database}/{table_name}"
    )


def ensure_schwab_futures(catalog: Catalog | None = None) -> Table:
    """Create `futures.schwab_futures` if absent; return the table."""
    catalog = catalog or get_catalog()
    _ensure_namespace(catalog)

    table_id = futures_table_id(FUTURES_TABLE_NAME)
    try:
        return catalog.load_table(table_id)
    except NoSuchTableError:
        pass

    location = _futures_table_location(FUTURES_TABLE_NAME)
    log.info("Creating Iceberg table %s at %s", table_id, location)
    return catalog.create_table(
        identifier=table_id,
        schema=FUTURES_OHLCV_SCHEMA,
        location=location,
        partition_spec=FUTURES_OHLCV_PARTITION,
        sort_order=FUTURES_OHLCV_SORT,
        properties={**_BASE_PROPERTIES, **_MOR_PROPERTIES},
    )
