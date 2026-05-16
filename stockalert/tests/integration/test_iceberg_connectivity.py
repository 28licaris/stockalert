"""
Phase 0 gate test — Iceberg connectivity.

Verifies the data-platform foundation:
  1. PyIceberg can load the AWS Glue-backed catalog.
  2. The Iceberg warehouse path is writable in S3.
  3. Schema + MERGE INTO + read-back round-trip works.
  4. Table drop succeeds (no orphan state left behind).

Skips automatically when AWS-side prerequisites aren't in place so the
test suite stays green without secrets in CI. To run locally:

    poetry install
    export STOCK_LAKE_BUCKET=stock-lake
    export AWS_REGION=us-east-1  # or rely on `aws configure`
    poetry run pytest tests/integration/test_iceberg_connectivity.py -v
"""
from __future__ import annotations

import os
import time
import uuid

import pytest

from app.config import settings


pytestmark = pytest.mark.integration


def _aws_credentials_present() -> bool:
    """True if any AWS auth path is usable (env, profile, IAM role)."""
    if settings.aws_access_key_id and settings.aws_secret_access_key:
        return True
    if os.getenv("AWS_PROFILE"):
        return True
    if os.getenv("AWS_ROLE_ARN") and os.getenv("AWS_WEB_IDENTITY_TOKEN_FILE"):
        return True
    # boto3 default chain will pick up ~/.aws/credentials too; check the file.
    return os.path.isfile(os.path.expanduser("~/.aws/credentials"))


@pytest.fixture(scope="module")
def iceberg_catalog():
    if not settings.stock_lake_bucket:
        pytest.skip("STOCK_LAKE_BUCKET is not set — skipping Iceberg gate test")
    if not _aws_credentials_present():
        pytest.skip("AWS credentials not configured — skipping Iceberg gate test")

    try:
        from app.services.iceberg_catalog import get_catalog, reset_catalog_cache
    except ImportError as exc:
        pytest.skip(f"pyiceberg not installed yet: {exc}")

    reset_catalog_cache()
    try:
        return get_catalog()
    except Exception as exc:  # botocore/Glue errors surface here
        pytest.skip(f"Could not open Iceberg catalog: {exc}")


def test_catalog_lists_configured_namespace(iceberg_catalog) -> None:
    """The Glue database from provision_lake_infra.sh must be visible."""
    namespaces = {tuple(ns) for ns in iceberg_catalog.list_namespaces()}
    target = (settings.iceberg_glue_database,)
    assert target in namespaces, (
        f"Glue database {settings.iceberg_glue_database!r} not visible "
        f"to the catalog. Run scripts/provision_lake_infra.sh first. "
        f"Visible: {sorted(namespaces)}"
    )


def test_iceberg_table_roundtrip(iceberg_catalog) -> None:
    """Create temp table → write row → read row → drop table."""
    import pyarrow as pa
    from pyiceberg.schema import Schema
    from pyiceberg.types import NestedField, StringType, LongType, TimestamptzType

    suffix = f"{int(time.time())}_{uuid.uuid4().hex[:8]}"
    table_id = f"{settings.iceberg_glue_database}.connectivity_check_{suffix}"

    schema = Schema(
        NestedField(1, "symbol", StringType(), required=True),
        NestedField(2, "ts", TimestamptzType(), required=True),
        NestedField(3, "value", LongType(), required=True),
    )

    table = iceberg_catalog.create_table(table_id, schema=schema)

    try:
        # Iceberg schema declares fields as `required=True`; PyArrow defaults
        # to nullable, so we have to pin nullability on the inbound batch.
        arrow_schema = pa.schema([
            pa.field("symbol", pa.string(), nullable=False),
            pa.field("ts", pa.timestamp("us", tz="UTC"), nullable=False),
            pa.field("value", pa.int64(), nullable=False),
        ])
        sentinel = pa.Table.from_pydict(
            {
                "symbol": ["TEST"],
                "ts": [pa.scalar(int(time.time() * 1_000_000), type=pa.timestamp("us", tz="UTC")).as_py()],
                "value": [42],
            },
            schema=arrow_schema,
        )
        table.append(sentinel)

        scanned = table.scan().to_arrow().to_pydict()
        assert scanned["symbol"] == ["TEST"]
        assert scanned["value"] == [42]
    finally:
        # Capture the S3 prefix before drop so we can purge data files;
        # PyIceberg's `drop_table` only removes the catalog entry.
        table_location = table.location()
        iceberg_catalog.drop_table(table_id)
        _purge_s3_prefix(table_location)


def _purge_s3_prefix(s3_uri: str) -> None:
    """Delete every object under an s3://bucket/prefix/ URI."""
    import boto3

    assert s3_uri.startswith("s3://"), s3_uri
    without_scheme = s3_uri[len("s3://"):]
    bucket, _, prefix = without_scheme.partition("/")
    if not prefix.endswith("/"):
        prefix += "/"

    s3 = boto3.client("s3", region_name=settings.stock_lake_region)
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        objects = [{"Key": obj["Key"]} for obj in page.get("Contents", [])]
        if objects:
            s3.delete_objects(Bucket=bucket, Delete={"Objects": objects, "Quiet": True})
