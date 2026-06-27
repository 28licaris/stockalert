"""
Idempotent Iceberg table creation for the architecture-v2 `equities`
namespace (Phase 1 / CV1).

Mirrors the role of `app/services/bronze/tables.py` for v1: every
operator script or startup hook can call these to ensure the right
table exists with the right schema / partition / sort spec. PyIceberg
raises `TableAlreadyExists` if you call `create_table` on an existing
table, so we check first.

The four ensure_*() functions are NOT wired into uvicorn startup in
CV1 — they're operator-callable (or unit-test-callable) only. The
wiring lands in a follow-up commit once the DDL has been reviewed.
"""
from __future__ import annotations

import logging

from pyiceberg.catalog import Catalog
from pyiceberg.exceptions import (
    NamespaceAlreadyExistsError,
    NoSuchTableError,
)
from pyiceberg.table import Table

from app.config import settings
from app.services.equities.schemas import (
    MARKET_CORP_ACTIONS_PARTITION,
    MARKET_CORP_ACTIONS_SCHEMA,
    MARKET_CORP_ACTIONS_SORT,
    MARKET_SPLITS_PARTITION,
    MARKET_SPLITS_SCHEMA,
    MARKET_SPLITS_SORT,
    POLYGON_RAW_PARTITION,
    POLYGON_RAW_SCHEMA,
    POLYGON_RAW_SORT,
    SCHWAB_UNIVERSE_PARTITION,
    SCHWAB_UNIVERSE_SCHEMA,
    SCHWAB_UNIVERSE_SORT,
    equities_table_id,
)
from app.services.iceberg_catalog import get_catalog

log = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Iceberg table properties (Gates 2-4)
# ─────────────────────────────────────────────────────────────────────
#
# 128 MB target file + 16 MB row groups + zstd compression match
# docs/architecture_v2/02_schema.md DDL. `write.distribution-mode=hash`
# avoids file-per-task explosion when Spark writes with our
# bucket-partitioned spec.
_BASE_PROPERTIES: dict[str, str] = {
    "format-version": "2",
    "write.parquet.compression-codec": "zstd",
    "write.distribution-mode": "hash",
    "write.target-file-size-bytes": str(128 * 1024 * 1024),
    "write.parquet.row-group-size-bytes": str(16 * 1024 * 1024),
}

# Adjusted tables get merge-on-read for incremental corp-action
# rewrites (a single corp action affects O(symbol_history) bars, not
# the whole table — merge-on-read avoids rewriting the full partition).
_MOR_PROPERTIES: dict[str, str] = {
    "write.merge.mode": "merge-on-read",
    "write.update.mode": "merge-on-read",
    "write.delete.mode": "merge-on-read",
}

# Corp-actions table is tiny and append-mostly — no MoR, smaller files.
_CORP_ACTIONS_PROPERTIES: dict[str, str] = {
    "format-version": "2",
    "write.parquet.compression-codec": "zstd",
    "write.distribution-mode": "hash",
    "write.target-file-size-bytes": str(64 * 1024 * 1024),
    "write.parquet.row-group-size-bytes": str(16 * 1024 * 1024),
}


def _ensure_namespace(catalog: Catalog) -> None:
    """Create the `equities` Glue database if absent.

    PyIceberg's Glue `list_namespaces(db)` lists child namespaces of
    `db` and returns `[]` when `db` itself is missing — it does NOT
    raise `NoSuchNamespaceError`. A list-then-create probe silently
    no-ops on a fresh environment, then `create_table` blows up with
    `Database not found`. Always attempt create; swallow already-exists.
    """
    db = settings.iceberg_equities_glue_database
    try:
        catalog.create_namespace(db)
        log.info("Created Iceberg namespace %s", db)
    except NamespaceAlreadyExistsError:
        pass


def _equities_table_location(table_name: str) -> str:
    """Compute the explicit S3 location for an equities table.

    Matches docs/architecture_v2/03_s3_layout.md (post-CV1 patch):
        s3://{bucket}/{warehouse_prefix}/{equities_db}/{table_name}/

    e.g. s3://<your-bucket>/iceberg/equities/polygon_raw/
    """
    return (
        f"s3://{settings.stock_lake_bucket}/"
        f"{settings.iceberg_warehouse_prefix}/"
        f"{settings.iceberg_equities_glue_database}/"
        f"{table_name}"
    )


def ensure_polygon_raw(catalog: Catalog | None = None) -> Table:
    """Create `equities.polygon_raw` if absent; return the table."""
    catalog = catalog or get_catalog()
    _ensure_namespace(catalog)

    table_id = equities_table_id("polygon_raw")
    try:
        return catalog.load_table(table_id)
    except NoSuchTableError:
        pass

    location = _equities_table_location("polygon_raw")
    log.info("Creating Iceberg table %s at %s", table_id, location)
    return catalog.create_table(
        identifier=table_id,
        schema=POLYGON_RAW_SCHEMA,
        location=location,
        partition_spec=POLYGON_RAW_PARTITION,
        sort_order=POLYGON_RAW_SORT,
        properties=_BASE_PROPERTIES,
    )


def ensure_schwab_universe(catalog: Catalog | None = None) -> Table:
    """Create `equities.schwab_universe` if absent; return the table."""
    catalog = catalog or get_catalog()
    _ensure_namespace(catalog)

    table_id = equities_table_id("schwab_universe")
    try:
        return catalog.load_table(table_id)
    except NoSuchTableError:
        pass

    location = _equities_table_location("schwab_universe")
    log.info("Creating Iceberg table %s at %s", table_id, location)
    return catalog.create_table(
        identifier=table_id,
        schema=SCHWAB_UNIVERSE_SCHEMA,
        location=location,
        partition_spec=SCHWAB_UNIVERSE_PARTITION,
        sort_order=SCHWAB_UNIVERSE_SORT,
        properties={**_BASE_PROPERTIES, **_MOR_PROPERTIES},
    )


def ensure_market_corp_actions(catalog: Catalog | None = None) -> Table:
    """Create `equities.market_corp_actions` if absent; return the table."""
    catalog = catalog or get_catalog()
    _ensure_namespace(catalog)

    table_id = equities_table_id("market_corp_actions")
    try:
        return catalog.load_table(table_id)
    except NoSuchTableError:
        pass

    location = _equities_table_location("market_corp_actions")
    log.info("Creating Iceberg table %s at %s", table_id, location)
    return catalog.create_table(
        identifier=table_id,
        schema=MARKET_CORP_ACTIONS_SCHEMA,
        location=location,
        partition_spec=MARKET_CORP_ACTIONS_PARTITION,
        sort_order=MARKET_CORP_ACTIONS_SORT,
        properties=_CORP_ACTIONS_PROPERTIES,
    )


def ensure_market_splits(catalog: Catalog | None = None) -> Table:
    """Create `equities.market_splits` if absent; return the table.

    Dedicated splits store (adjustment input) — kept separate from the
    ~3M-row market_corp_actions so split lookups don't scan dividends.
    See docs/market_splits_spec.md.
    """
    catalog = catalog or get_catalog()
    _ensure_namespace(catalog)

    table_id = equities_table_id("market_splits")
    try:
        return catalog.load_table(table_id)
    except NoSuchTableError:
        pass

    location = _equities_table_location("market_splits")
    log.info("Creating Iceberg table %s at %s", table_id, location)
    return catalog.create_table(
        identifier=table_id,
        schema=MARKET_SPLITS_SCHEMA,
        location=location,
        partition_spec=MARKET_SPLITS_PARTITION,
        sort_order=MARKET_SPLITS_SORT,
        properties=_CORP_ACTIONS_PROPERTIES,
    )


def ensure_all(catalog: Catalog | None = None) -> dict[str, Table]:
    """Convenience: create all v2 equities tables.

    Returns {table_name: Table}. Idempotent — safe to call repeatedly.
    Intended for operator scripts; not wired into uvicorn startup yet
    (see follow-up CV1b). `polygon_adjusted` is intentionally absent —
    adjusted OHLCV is computed at read time (lean storage migration).
    """
    catalog = catalog or get_catalog()
    return {
        "polygon_raw": ensure_polygon_raw(catalog),
        "schwab_universe": ensure_schwab_universe(catalog),
        "market_corp_actions": ensure_market_corp_actions(catalog),
        "market_splits": ensure_market_splits(catalog),
    }


# Dispatch table for runtime callers that know the table name as a
# string (e.g. live writers parameterized by config). Keep in sync with
# the ensure_*() functions above.
_ENSURE_DISPATCH: dict[str, "callable[[Catalog | None], Table]"] = {
    "polygon_raw": ensure_polygon_raw,
    "schwab_universe": ensure_schwab_universe,
    "market_corp_actions": ensure_market_corp_actions,
    "market_splits": ensure_market_splits,
}


def ensure_equities_table(
    table_name: str, catalog: Catalog | None = None
) -> Table:
    """Idempotent ensure-by-short-name for callers parameterized at runtime.

    The static ensure_*() functions are preferred when the table is known
    at write-time. This dispatcher exists for callers like the live lake
    writer that pick the table from config — they need an idempotent
    create-if-missing without a four-way `if/elif` on every cycle.

    Raises ValueError on unknown short_name rather than silently creating
    a bogus table (NO_SILENT_FAILURES).
    """
    try:
        fn = _ENSURE_DISPATCH[table_name]
    except KeyError:
        raise ValueError(
            f"Unknown equities table short_name: {table_name!r}. "
            f"Known: {sorted(_ENSURE_DISPATCH)}"
        ) from None
    return fn(catalog)
