"""
Iceberg catalog helper.

Single entry point for opening the AWS Glue-backed Iceberg catalog used
by the data platform (bronze/silver/gold). Loads connection params from
`app.config.settings`; no module reads PyIceberg config files directly.

Usage:
    from app.services.iceberg_catalog import get_catalog
    catalog = get_catalog()
    catalog.list_namespaces()
"""
from __future__ import annotations

from functools import lru_cache

from pyiceberg.catalog import Catalog, load_catalog

from app.config import settings


def _build_catalog_properties() -> dict[str, str]:
    """Build the PyIceberg properties dict from app settings."""
    if not settings.stock_lake_bucket:
        raise RuntimeError(
            "STOCK_LAKE_BUCKET is not configured. Set it in .env before "
            "opening the Iceberg catalog."
        )

    warehouse = (
        f"s3://{settings.stock_lake_bucket}/{settings.iceberg_warehouse_prefix}/"
    )

    properties: dict[str, str] = {
        "type": "glue",
        "warehouse": warehouse,
        "glue.region": settings.stock_lake_region,
        "s3.region": settings.stock_lake_region,
    }

    # If explicit creds are provided, pass them through. Otherwise PyIceberg
    # falls through to the default boto3 credential chain (env / profile /
    # IAM role).
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        properties["s3.access-key-id"] = settings.aws_access_key_id
        properties["s3.secret-access-key"] = settings.aws_secret_access_key
        if settings.aws_session_token:
            properties["s3.session-token"] = settings.aws_session_token
        properties["glue.access-key-id"] = settings.aws_access_key_id
        properties["glue.secret-access-key"] = settings.aws_secret_access_key
        if settings.aws_session_token:
            properties["glue.session-token"] = settings.aws_session_token

    return properties


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    """
    Open (and cache) the Iceberg catalog.

    Catalog name follows ICEBERG_CATALOG_NAME so multiple catalogs can
    coexist later (e.g. a separate dev catalog).
    """
    return load_catalog(settings.iceberg_catalog_name, **_build_catalog_properties())


def reset_catalog_cache() -> None:
    """Clear the cached catalog. Useful in tests that mutate settings."""
    get_catalog.cache_clear()
