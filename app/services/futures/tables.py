"""Create the futures Glue DB + Iceberg tables (idempotent).

Mirrors `app/services/equities/tables.py` but for the `futures` Glue DB
and `iceberg/futures/` S3 location.

Tables:
  futures.schwab_futures         — 1-min OHLCV from Schwab (~48-day window)
  futures.schwab_futures_daily   — Daily OHLCV from Schwab (years of history)
  futures.polygon_futures        — 1-min OHLCV from Polygon (deep history)
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
    POLYGON_RAW_PARTITION,
    POLYGON_RAW_SCHEMA,
    POLYGON_RAW_SORT,
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


POLYGON_FUTURES_TABLE_NAME = "polygon_futures"


def ensure_polygon_futures(catalog: Catalog | None = None) -> Table:
    """Create `futures.polygon_futures` if absent; return it.

    Deep-history 1-min OHLCV from Polygon per-contract pulls stitched into
    continuous roots. Same column shape as schwab_futures; source tag is
    'polygon-futures'. Populated by scripts/polygon_futures_backfill.py."""
    catalog = catalog or get_catalog()
    _ensure_namespace(catalog)

    table_id = futures_table_id(POLYGON_FUTURES_TABLE_NAME)
    try:
        return catalog.load_table(table_id)
    except NoSuchTableError:
        pass

    location = _futures_table_location(POLYGON_FUTURES_TABLE_NAME)
    log.info("Creating Iceberg table %s at %s", table_id, location)
    return catalog.create_table(
        identifier=table_id,
        schema=FUTURES_OHLCV_SCHEMA,
        location=location,
        partition_spec=FUTURES_OHLCV_PARTITION,
        sort_order=FUTURES_OHLCV_SORT,
        properties={**_BASE_PROPERTIES, **_MOR_PROPERTIES},
    )


POLYGON_RAW_TABLE_NAME = "polygon_raw"


def ensure_polygon_raw(catalog: Catalog | None = None) -> Table:
    """Create `futures.polygon_raw` if absent; return it.

    Per-CONTRACT raw 1-min OHLCV (outright contracts ESH4, CLM4, …) parsed
    from the flat-file mirror — no roll, no adjustment. Analog of
    `equities.polygon_raw`. Partitioned by identity(root) + month(timestamp).
    Populated by scripts/polygon_futures_parse_raw.py; the continuous-root
    layer (futures.polygon_continuous) is derived from this table."""
    catalog = catalog or get_catalog()
    _ensure_namespace(catalog)

    table_id = futures_table_id(POLYGON_RAW_TABLE_NAME)
    try:
        return catalog.load_table(table_id)
    except NoSuchTableError:
        pass

    location = _futures_table_location(POLYGON_RAW_TABLE_NAME)
    log.info("Creating Iceberg table %s at %s", table_id, location)
    return catalog.create_table(
        identifier=table_id,
        schema=POLYGON_RAW_SCHEMA,
        location=location,
        partition_spec=POLYGON_RAW_PARTITION,
        sort_order=POLYGON_RAW_SORT,
        properties={**_BASE_PROPERTIES, **_MOR_PROPERTIES},
    )


FUTURES_DAILY_TABLE_NAME = "schwab_futures_daily"


def ensure_schwab_futures_daily(catalog: Catalog | None = None) -> Table:
    """Create `futures.schwab_futures_daily` if absent; return it.

    Deep-history DAILY OHLCV for continuous roots, pulled directly from Schwab
    (`frequencyType=daily`). Schwab caps *minute* history at ~48 days but serves
    years of daily — so this is the tier that feeds daily consumers (charts,
    Elliott Wave) beyond the 1-minute window. Same column shape as
    `schwab_futures`; only the resolution + cadence differ."""
    catalog = catalog or get_catalog()
    _ensure_namespace(catalog)

    table_id = futures_table_id(FUTURES_DAILY_TABLE_NAME)
    try:
        return catalog.load_table(table_id)
    except NoSuchTableError:
        pass

    location = _futures_table_location(FUTURES_DAILY_TABLE_NAME)
    log.info("Creating Iceberg table %s at %s", table_id, location)
    return catalog.create_table(
        identifier=table_id,
        schema=FUTURES_OHLCV_SCHEMA,
        location=location,
        partition_spec=FUTURES_OHLCV_PARTITION,
        sort_order=FUTURES_OHLCV_SORT,
        properties={**_BASE_PROPERTIES, **_MOR_PROPERTIES},
    )
