#!/usr/bin/env python3
"""
Smoke test the ``stock-lake`` S3 connection end-to-end.

Validates every operation the lake archive service relies on:

    1. ``head_bucket`` (auth + region)
    2. ``put_bytes`` (small text object)
    3. ``head_object`` (round-trip metadata)
    4. ``get_bytes`` (round-trip content)
    5. ``list_prefix`` (pagination contract)
    6. ``put_parquet`` (DataFrame -> Parquet + Snappy)
    7. ``delete`` and ``delete_many`` (cleanup)

Everything goes under the ``_healthcheck/`` prefix and is deleted on success
so the bucket stays clean. Reuses the production ``S3LakeClient`` so a
green run here means the in-app code path is equivalently configured.

Requires in .env:
    STOCK_LAKE_BUCKET=<your bucket>
    STOCK_LAKE_REGION=us-east-1
    AWS_ACCESS_KEY_ID=<...>
    AWS_SECRET_ACCESS_KEY=<...>

Run from the project root (stockalert/stockalert):

    poetry run python scripts/check_s3_lake.py
"""
from __future__ import annotations

import sys
import time
from datetime import datetime, timezone

import pandas as pd
from dotenv import load_dotenv

load_dotenv()

from app.config import settings  # noqa: E402
from app.services.legacy.s3_lake_client import S3LakeClient, S3LakeClientError  # noqa: E402


# Use a single timestamped subprefix so concurrent runs never collide.
RUN_PREFIX = f"_healthcheck/{int(time.time())}"
TEXT_KEY = f"{RUN_PREFIX}/hello.txt"
PARQUET_KEY = f"{RUN_PREFIX}/sample.parquet"


def _fail(msg: str, exc: Exception | None = None) -> None:
    print(f"FAIL: {msg}")
    if exc is not None:
        print(f"      cause: {exc}")
    sys.exit(1)


def main() -> None:
    if not settings.stock_lake_bucket:
        _fail("STOCK_LAKE_BUCKET is not set in .env")

    print(f"Bucket: {settings.stock_lake_bucket}")
    print(f"Region: {settings.stock_lake_region}")
    print(f"Creds : {'env' if settings.aws_access_key_id else 'default chain (~/.aws/...)'}")
    print()

    try:
        client = S3LakeClient.from_settings()
    except Exception as e:
        _fail("S3LakeClient.from_settings()", e)

    # ── 1. put_bytes ──────────────────────────────────────────────
    payload = (
        f"stockalert lake healthcheck @ "
        f"{datetime.now(timezone.utc).isoformat()}\n"
    ).encode("utf-8")
    print(f"1. put_bytes  -> {TEXT_KEY}")
    try:
        n = client.put_bytes(
            TEXT_KEY, payload, content_type="text/plain",
            metadata={"source": "smoke-test"},
        )
    except S3LakeClientError as e:
        _fail("put_bytes", e)
    print(f"   uploaded {n} bytes")

    # ── 2. head ───────────────────────────────────────────────────
    print(f"2. head       -> {TEXT_KEY}")
    try:
        meta = client.head(TEXT_KEY)
    except S3LakeClientError as e:
        _fail("head", e)
    if meta is None:
        _fail("head: object disappeared immediately after put_bytes")
    print(f"   size={meta.get('ContentLength')}  etag={meta.get('ETag')}")

    # ── 3. get_bytes round-trip ───────────────────────────────────
    print(f"3. get_bytes  -> {TEXT_KEY}")
    try:
        body = client.get_bytes(TEXT_KEY)
    except S3LakeClientError as e:
        _fail("get_bytes", e)
    if body != payload:
        _fail(f"get_bytes: round-trip mismatch (got {len(body)} bytes, expected {len(payload)})")
    print(f"   {len(body)} bytes round-tripped OK")

    # ── 4. list_prefix ────────────────────────────────────────────
    print(f"4. list_prefix-> {RUN_PREFIX}/")
    try:
        objs = list(client.list_prefix(f"{RUN_PREFIX}/"))
    except S3LakeClientError as e:
        _fail("list_prefix", e)
    found = {o.key for o in objs}
    if TEXT_KEY not in found:
        _fail(f"list_prefix: {TEXT_KEY!r} missing from listing ({len(objs)} objects)")
    print(f"   {len(objs)} object(s) under prefix; healthcheck file present")

    # ── 5. put_parquet ────────────────────────────────────────────
    print(f"5. put_parquet-> {PARQUET_KEY}")
    df = pd.DataFrame(
        {
            "symbol":    ["AAPL", "MSFT", "GOOGL"],
            "open":      [225.0, 410.5, 175.25],
            "close":     [226.10, 411.0, 175.50],
            "volume":    [1_000_000, 750_000, 500_000],
            "timestamp": pd.to_datetime(
                ["2026-05-13T14:30:00Z",
                 "2026-05-13T14:31:00Z",
                 "2026-05-13T14:32:00Z"],
                utc=True,
            ),
        }
    ).set_index("timestamp")
    try:
        n = client.put_parquet(PARQUET_KEY, df, metadata={"source": "smoke-test"})
    except S3LakeClientError as e:
        _fail("put_parquet", e)
    print(f"   uploaded {n} bytes (Parquet/Snappy, {len(df)} rows)")

    # Verify Parquet is readable back.
    try:
        raw = client.get_bytes(PARQUET_KEY)
    except S3LakeClientError as e:
        _fail("get_bytes(parquet)", e)
    try:
        import io

        df2 = pd.read_parquet(io.BytesIO(raw), engine="pyarrow")
    except Exception as e:
        _fail("parquet round-trip parse", e)
    if len(df2) != len(df):
        _fail(f"parquet round-trip row count mismatch: got {len(df2)}, expected {len(df)}")
    print(f"   parquet re-read OK: {len(df2)} rows, columns={list(df2.columns)}")

    # ── 6. delete_many cleanup ────────────────────────────────────
    print(f"6. delete_many-> {RUN_PREFIX}/*")
    try:
        deleted = client.delete_many([o.key for o in objs] + [PARQUET_KEY])
    except S3LakeClientError as e:
        _fail("delete_many", e)
    print(f"   deleted {deleted} key(s)")

    # ── 7. confirm gone ───────────────────────────────────────────
    print(f"7. head(after delete) -> {TEXT_KEY}")
    try:
        meta = client.head(TEXT_KEY)
    except S3LakeClientError as e:
        _fail("head after delete", e)
    if meta is not None:
        _fail(f"head: object {TEXT_KEY!r} still exists after delete_many")
    print("   gone (None as expected)")

    print()
    print(f"PASS: s3://{settings.stock_lake_bucket}/ is fully wired (RW + delete).")


if __name__ == "__main__":
    main()
