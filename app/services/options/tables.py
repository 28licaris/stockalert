"""Create the options Glue DB + Iceberg tables (idempotent)."""
from __future__ import annotations

import logging
from collections.abc import Callable

from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import NamespaceAlreadyExistsError, NoSuchTableError
from pyiceberg.partitioning import PartitionField, PartitionSpec
from pyiceberg.schema import Schema
from pyiceberg.table import Table
from pyiceberg.table.sorting import NullOrder, SortDirection, SortField, SortOrder
from pyiceberg.transforms import BucketTransform, IdentityTransform, MonthTransform
from pyiceberg.types import (
    BooleanType,
    DateType,
    DoubleType,
    IntegerType,
    LongType,
    NestedField,
    StringType,
    TimestamptzType,
)

from app.config import settings
from app.services.equities.tables import _BASE_PROPERTIES, _MOR_PROPERTIES
from app.services.iceberg_catalog import get_catalog

log = logging.getLogger(__name__)

CHAIN_RAW_TABLE_NAME = "schwab_chain_raw"
CHAIN_CONTRACTS_TABLE_NAME = "schwab_chain_contracts"
EXPIRATIONS_TABLE_NAME = "schwab_expirations"
GAMMA_EXPOSURE_TABLE_NAME = "gamma_exposure_snapshots"


def options_table_id(name: str) -> str:
    """Fully-qualified PyIceberg table id (`<options_glue_db>.<name>`)."""
    return f"{settings.iceberg_options_glue_database}.{name}"


CHAIN_RAW_SCHEMA = Schema(
    NestedField(1, "underlying_symbol", StringType(), required=True),
    NestedField(2, "snapshot_ts", TimestamptzType(), required=True),
    NestedField(3, "provider", StringType(), required=True),
    NestedField(4, "request_params", StringType(), required=True),
    NestedField(5, "status", StringType(), required=True),
    NestedField(6, "is_delayed", BooleanType(), required=False),
    NestedField(7, "underlying_price", DoubleType(), required=False),
    NestedField(8, "raw_payload", StringType(), required=True),
    NestedField(9, "ingestion_ts", TimestamptzType(), required=True),
    NestedField(10, "ingestion_run_id", StringType(), required=True),
    identifier_field_ids=[1, 2, 3],
)

CHAIN_RAW_PARTITION = PartitionSpec(
    PartitionField(source_id=2, field_id=1000, transform=MonthTransform(), name="snapshot_month"),
)

CHAIN_RAW_SORT = SortOrder(
    SortField(source_id=1, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=2, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
)

CHAIN_CONTRACTS_SCHEMA = Schema(
    NestedField(1, "underlying_symbol", StringType(), required=True),
    NestedField(2, "option_symbol", StringType(), required=True),
    NestedField(3, "snapshot_ts", TimestamptzType(), required=True),
    NestedField(4, "put_call", StringType(), required=True),
    NestedField(5, "expiration_date", DateType(), required=True),
    NestedField(6, "strike", DoubleType(), required=True),
    NestedField(7, "underlying_price", DoubleType(), required=False),
    NestedField(8, "days_to_expiration", IntegerType(), required=False),
    NestedField(9, "bid", DoubleType(), required=False),
    NestedField(10, "ask", DoubleType(), required=False),
    NestedField(11, "last", DoubleType(), required=False),
    NestedField(12, "mark", DoubleType(), required=False),
    NestedField(13, "bid_size", LongType(), required=False),
    NestedField(14, "ask_size", LongType(), required=False),
    NestedField(15, "last_size", LongType(), required=False),
    NestedField(16, "volume", LongType(), required=False),
    NestedField(17, "open_interest", LongType(), required=False),
    NestedField(18, "quote_time", TimestamptzType(), required=False),
    NestedField(19, "trade_time", TimestamptzType(), required=False),
    NestedField(20, "delta", DoubleType(), required=False),
    NestedField(21, "gamma", DoubleType(), required=False),
    NestedField(22, "theta", DoubleType(), required=False),
    NestedField(23, "vega", DoubleType(), required=False),
    NestedField(24, "rho", DoubleType(), required=False),
    NestedField(25, "volatility", DoubleType(), required=False),
    NestedField(26, "theoretical_value", DoubleType(), required=False),
    NestedField(27, "intrinsic_value", DoubleType(), required=False),
    NestedField(28, "time_value", DoubleType(), required=False),
    NestedField(29, "in_the_money", BooleanType(), required=False),
    NestedField(30, "mini", BooleanType(), required=False),
    NestedField(31, "non_standard", BooleanType(), required=False),
    NestedField(32, "penny_pilot", BooleanType(), required=False),
    NestedField(33, "multiplier", DoubleType(), required=False),
    NestedField(34, "settlement_type", StringType(), required=False),
    NestedField(35, "expiration_type", StringType(), required=False),
    NestedField(36, "source", StringType(), required=True),
    NestedField(37, "ingestion_ts", TimestamptzType(), required=True),
    NestedField(38, "ingestion_run_id", StringType(), required=True),
    identifier_field_ids=[1, 2, 3],
)

CHAIN_CONTRACTS_PARTITION = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=BucketTransform(16), name="underlying_bucket"),
    PartitionField(source_id=3, field_id=1001, transform=MonthTransform(), name="snapshot_month"),
)

CHAIN_CONTRACTS_SORT = SortOrder(
    SortField(source_id=1, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=5, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=6, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=4, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=3, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
)

EXPIRATIONS_SCHEMA = Schema(
    NestedField(1, "underlying_symbol", StringType(), required=True),
    NestedField(2, "expiration_date", DateType(), required=True),
    NestedField(3, "days_to_expiration", IntegerType(), required=False),
    NestedField(4, "expiration_type", StringType(), required=False),
    NestedField(5, "settlement_type", StringType(), required=False),
    NestedField(6, "source", StringType(), required=True),
    NestedField(7, "observed_ts", TimestamptzType(), required=True),
    NestedField(8, "ingestion_ts", TimestamptzType(), required=True),
    NestedField(9, "ingestion_run_id", StringType(), required=True),
    identifier_field_ids=[1, 2, 7],
)

EXPIRATIONS_PARTITION = PartitionSpec(
    PartitionField(source_id=2, field_id=1000, transform=MonthTransform(), name="expiration_month"),
)

EXPIRATIONS_SORT = SortOrder(
    SortField(source_id=1, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=2, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=7, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
)

GAMMA_EXPOSURE_SCHEMA = Schema(
    NestedField(1, "underlying_symbol", StringType(), required=True),
    NestedField(2, "snapshot_ts", TimestamptzType(), required=True),
    NestedField(3, "expiration_date", DateType(), required=False),
    NestedField(4, "strike", DoubleType(), required=False),
    NestedField(5, "put_call", StringType(), required=False),
    NestedField(6, "underlying_price", DoubleType(), required=True),
    NestedField(7, "gamma_exposure", DoubleType(), required=True),
    NestedField(8, "call_gamma_exposure", DoubleType(), required=False),
    NestedField(9, "put_gamma_exposure", DoubleType(), required=False),
    NestedField(10, "net_gamma_exposure", DoubleType(), required=False),
    NestedField(11, "open_interest", LongType(), required=False),
    NestedField(12, "volume", LongType(), required=False),
    NestedField(13, "contract_count", LongType(), required=False),
    NestedField(14, "aggregation_level", StringType(), required=True),
    NestedField(15, "level_key", StringType(), required=True),
    NestedField(16, "methodology", StringType(), required=True),
    NestedField(17, "source", StringType(), required=True),
    NestedField(18, "source_snapshot_id", StringType(), required=False),
    NestedField(19, "ingestion_ts", TimestamptzType(), required=True),
    NestedField(20, "ingestion_run_id", StringType(), required=True),
    identifier_field_ids=[1, 2, 14, 15],
)

GAMMA_EXPOSURE_PARTITION = PartitionSpec(
    PartitionField(source_id=1, field_id=1000, transform=BucketTransform(16), name="underlying_bucket"),
    PartitionField(source_id=2, field_id=1001, transform=MonthTransform(), name="snapshot_month"),
)

GAMMA_EXPOSURE_SORT = SortOrder(
    SortField(source_id=1, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=2, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=14, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=15, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=3, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
    SortField(source_id=4, transform=IdentityTransform(), direction=SortDirection.ASC, null_order=NullOrder.NULLS_LAST),
)


def _ensure_namespace(catalog: Catalog) -> None:
    db = settings.iceberg_options_glue_database
    try:
        catalog.create_namespace(db)
        log.info("Created Iceberg namespace %s", db)
    except NamespaceAlreadyExistsError:
        pass


def _options_table_location(table_name: str) -> str:
    return (
        f"s3://{settings.stock_lake_bucket}/{settings.iceberg_warehouse_prefix}/"
        f"{settings.iceberg_options_glue_database}/{table_name}"
    )


def _ensure_table(
    *,
    table_name: str,
    schema: Schema,
    partition_spec: PartitionSpec,
    sort_order: SortOrder,
    catalog: Catalog | None,
) -> Table:
    catalog = catalog or get_catalog()
    _ensure_namespace(catalog)
    table_id = options_table_id(table_name)
    try:
        return catalog.load_table(table_id)
    except NoSuchTableError:
        pass

    location = _options_table_location(table_name)
    log.info("Creating Iceberg table %s at %s", table_id, location)
    return catalog.create_table(
        identifier=table_id,
        schema=schema,
        location=location,
        partition_spec=partition_spec,
        sort_order=sort_order,
        properties={**_BASE_PROPERTIES, **_MOR_PROPERTIES},
    )


def ensure_chain_raw(catalog: Catalog | None = None) -> Table:
    return _ensure_table(
        table_name=CHAIN_RAW_TABLE_NAME,
        schema=CHAIN_RAW_SCHEMA,
        partition_spec=CHAIN_RAW_PARTITION,
        sort_order=CHAIN_RAW_SORT,
        catalog=catalog,
    )


def ensure_chain_contracts(catalog: Catalog | None = None) -> Table:
    return _ensure_table(
        table_name=CHAIN_CONTRACTS_TABLE_NAME,
        schema=CHAIN_CONTRACTS_SCHEMA,
        partition_spec=CHAIN_CONTRACTS_PARTITION,
        sort_order=CHAIN_CONTRACTS_SORT,
        catalog=catalog,
    )


def ensure_expirations(catalog: Catalog | None = None) -> Table:
    return _ensure_table(
        table_name=EXPIRATIONS_TABLE_NAME,
        schema=EXPIRATIONS_SCHEMA,
        partition_spec=EXPIRATIONS_PARTITION,
        sort_order=EXPIRATIONS_SORT,
        catalog=catalog,
    )


def ensure_gamma_exposure(catalog: Catalog | None = None) -> Table:
    return _ensure_table(
        table_name=GAMMA_EXPOSURE_TABLE_NAME,
        schema=GAMMA_EXPOSURE_SCHEMA,
        partition_spec=GAMMA_EXPOSURE_PARTITION,
        sort_order=GAMMA_EXPOSURE_SORT,
        catalog=catalog,
    )


def ensure_all(catalog: Catalog | None = None) -> dict[str, Table]:
    catalog = catalog or get_catalog()
    return {
        CHAIN_RAW_TABLE_NAME: ensure_chain_raw(catalog),
        CHAIN_CONTRACTS_TABLE_NAME: ensure_chain_contracts(catalog),
        EXPIRATIONS_TABLE_NAME: ensure_expirations(catalog),
        GAMMA_EXPOSURE_TABLE_NAME: ensure_gamma_exposure(catalog),
    }


_ENSURE_DISPATCH: dict[str, Callable[[Catalog | None], Table]] = {
    CHAIN_RAW_TABLE_NAME: ensure_chain_raw,
    CHAIN_CONTRACTS_TABLE_NAME: ensure_chain_contracts,
    EXPIRATIONS_TABLE_NAME: ensure_expirations,
    GAMMA_EXPOSURE_TABLE_NAME: ensure_gamma_exposure,
}


def ensure_options_table(table_name: str, catalog: Catalog | None = None) -> Table:
    try:
        fn = _ENSURE_DISPATCH[table_name]
    except KeyError:
        raise ValueError(
            f"Unknown options table short_name: {table_name!r}. "
            f"Known: {sorted(_ENSURE_DISPATCH)}"
        ) from None
    return fn(catalog)
