"""
Thin, self-contained boto3 wrapper for the ``stock-lake`` S3 bucket.

This module is **transport-only**. It knows nothing about ClickHouse, the
backfill scheduler, or the seed universe — that separation is deliberate so
the client can be:

  - reused from scripts (``scripts/polygon_flatfiles_bulk_backfill.py``)
  - reused from other services (``LakeArchiveService``, future compactors)
  - hosted in its own process / pod if the lake archive ever grows out of
    this monolith, because there's no in-process coupling to FastAPI

Construction is explicit (``S3LakeClient(bucket=..., region=..., ...)``); the
``from_settings()`` factory builds the canonical instance from `app.config`
and is the *only* point that depends on the global ``settings`` object.

Credentials fall back to the default boto3 credential chain (env vars,
``~/.aws/credentials``, EC2/ECS task role) when the explicit keys are empty,
so a deployed container needs only ``STOCK_LAKE_BUCKET`` set in its env.
"""
from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Iterator, Optional

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class S3Object:
    """Minimal, library-agnostic view of one S3 object listing result."""
    key: str
    size: int
    last_modified: Any  # datetime.datetime in UTC; left as Any so we don't
                       # import datetime at module top just for a type hint.


class S3LakeClientError(RuntimeError):
    """Raised on any underlying boto3/ClientError. Re-thrown so callers can
    treat lake errors as a single category instead of unpacking botocore."""


class S3LakeClient:
    """
    Bucket-scoped S3 wrapper for the user's data lake.

    Only the operations we need land here:
      - ``put_parquet``  — write a DataFrame as Parquet (Snappy)
      - ``put_bytes``    — write arbitrary bytes (used by future compactors)
      - ``head``         — exists / metadata check
      - ``get_bytes``    — read raw bytes (mostly for tests / verification)
      - ``list_prefix``  — paginated listing under a prefix
      - ``delete``       — remove a single key (compaction cleanup)

    All methods are synchronous. Call sites that need async (FastAPI routes,
    background tasks) wrap them in ``asyncio.to_thread`` rather than dragging
    aiobotocore into the dependency graph.
    """

    DEFAULT_REGION = "us-east-1"

    def __init__(
        self,
        *,
        bucket: str,
        region: str = DEFAULT_REGION,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        session_token: Optional[str] = None,
        endpoint_url: Optional[str] = None,
        client: Any | None = None,
    ) -> None:
        if not bucket:
            raise ValueError("S3LakeClient: bucket is required")
        self._bucket = bucket
        self._region = region or self.DEFAULT_REGION
        self._endpoint_url = endpoint_url
        # Empty-string creds are treated as "fall back to the default boto3
        # credential chain". This lets EC2 / ECS deploys skip the AWS_* env
        # vars entirely and use IAM task roles.
        self._access_key_id = access_key_id or None
        self._secret_access_key = secret_access_key or None
        self._session_token = session_token or None
        # Lazy boto3 client. Tests inject ``client=`` directly so they never
        # need network access or AWS creds.
        self._client = client

    # ---------- factory ----------

    @classmethod
    def from_settings(cls) -> "S3LakeClient":
        """
        Build from ``app.config.settings``. Raises ``ValueError`` if the
        ``STOCK_LAKE_BUCKET`` env var isn't set — caller decides whether
        that's fatal or a warn-and-continue.
        """
        from app.config import settings
        return cls(
            bucket=settings.stock_lake_bucket,
            region=settings.stock_lake_region,
            access_key_id=settings.aws_access_key_id,
            secret_access_key=settings.aws_secret_access_key,
            session_token=settings.aws_session_token,
        )

    # ---------- properties ----------

    @property
    def bucket(self) -> str:
        return self._bucket

    @property
    def region(self) -> str:
        return self._region

    # ---------- internal ----------

    def _get_client(self) -> Any:
        """Lazy-build the underlying boto3 S3 client. Done lazily so importing
        this module doesn't require boto3 (and tests can inject a mock without
        ever importing boto3 at all)."""
        if self._client is None:
            import boto3
            kwargs: dict[str, Any] = {"region_name": self._region}
            if self._endpoint_url:
                kwargs["endpoint_url"] = self._endpoint_url
            if self._access_key_id and self._secret_access_key:
                kwargs["aws_access_key_id"] = self._access_key_id
                kwargs["aws_secret_access_key"] = self._secret_access_key
                if self._session_token:
                    kwargs["aws_session_token"] = self._session_token
            self._client = boto3.client("s3", **kwargs)
        return self._client

    def _wrap_error(self, op: str, key: str, exc: Exception) -> S3LakeClientError:
        """Normalize botocore/boto3 errors into a single exception type. The
        caller is responsible for ``raise ... from exc`` so the cause chain
        is preserved (Python doesn't allow ``return X from Y``)."""
        return S3LakeClientError(
            f"S3 {op} failed for s3://{self._bucket}/{key}: {exc}"
        )

    # ---------- public API ----------

    def put_parquet(
        self,
        key: str,
        df: pd.DataFrame,
        *,
        compression: str = "snappy",
        metadata: Optional[dict[str, str]] = None,
    ) -> int:
        """
        Serialize ``df`` to Parquet in-memory and PUT it at ``key``.

        Returns the number of bytes uploaded. Empty DataFrames are rejected
        (returning ``0`` instead would create empty Parquet files in the lake
        that complicate compaction queries). Callers that genuinely want a
        zero-row marker should use ``put_bytes`` with their own sentinel.

        ``metadata`` becomes S3 object metadata (``x-amz-meta-*`` headers) and
        is the right place to record provenance hints — e.g.
        ``{"source": "polygon-flatfiles", "bars": "42000"}`` — that aren't
        cheap to compute from the Parquet itself.
        """
        if df is None or df.empty:
            raise ValueError(
                f"put_parquet refusing to write empty DataFrame to {key!r}"
            )
        buf = io.BytesIO()
        df.to_parquet(buf, engine="pyarrow", compression=compression, index=True)
        body = buf.getvalue()
        try:
            extra: dict[str, Any] = {}
            if metadata:
                extra["Metadata"] = {k: str(v) for k, v in metadata.items()}
            self._get_client().put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType="application/octet-stream",
                **extra,
            )
        except Exception as e:
            raise self._wrap_error("put_parquet", key, e) from e
        return len(body)

    def put_bytes(
        self,
        key: str,
        body: bytes,
        *,
        content_type: str = "application/octet-stream",
        metadata: Optional[dict[str, str]] = None,
    ) -> int:
        """Generic byte-level upload. Used by compactors writing pre-serialized
        Parquet via ``pyarrow.dataset`` and by tests."""
        try:
            extra: dict[str, Any] = {}
            if metadata:
                extra["Metadata"] = {k: str(v) for k, v in metadata.items()}
            self._get_client().put_object(
                Bucket=self._bucket,
                Key=key,
                Body=body,
                ContentType=content_type,
                **extra,
            )
        except Exception as e:
            raise self._wrap_error("put_bytes", key, e) from e
        return len(body)

    def head(self, key: str) -> Optional[dict[str, Any]]:
        """
        Return object metadata if the key exists, ``None`` if not. Any other
        client error is wrapped and re-raised so callers don't silently
        treat a permission failure as "object missing".
        """
        try:
            return self._get_client().head_object(Bucket=self._bucket, Key=key)
        except Exception as e:
            # botocore.exceptions.ClientError with Code=404/NoSuchKey is the
            # canonical "doesn't exist". We avoid importing botocore at module
            # scope, so duck-type the response shape instead.
            code = ""
            response = getattr(e, "response", None)
            if isinstance(response, dict):
                code = response.get("Error", {}).get("Code", "") or ""
                http_status = response.get("ResponseMetadata", {}).get(
                    "HTTPStatusCode"
                )
                if str(code) in {"404", "NoSuchKey", "NotFound"} or http_status == 404:
                    return None
            raise self._wrap_error("head", key, e) from e

    def exists(self, key: str) -> bool:
        """Convenience wrapper for ``head(key) is not None``. Cheaper than
        list_prefix when callers only need a presence check."""
        return self.head(key) is not None

    def get_bytes(self, key: str) -> bytes:
        """
        Read an object back as bytes. Mainly used by tests / smoke scripts;
        production reads of Parquet should use ``pyarrow.dataset`` directly
        for streaming.
        """
        try:
            resp = self._get_client().get_object(Bucket=self._bucket, Key=key)
            body = resp["Body"]
            try:
                return body.read()
            finally:
                close = getattr(body, "close", None)
                if callable(close):
                    close()
        except Exception as e:
            raise self._wrap_error("get_bytes", key, e) from e

    def list_prefix(
        self,
        prefix: str,
        *,
        page_size: int = 1000,
    ) -> Iterator[S3Object]:
        """
        Yield every object under ``prefix``. Paginates internally so callers
        don't worry about the 1000-key List V2 cap. Items are yielded in
        S3's natural sort order (lexicographic by key).
        """
        client = self._get_client()
        continuation: Optional[str] = None
        while True:
            kwargs: dict[str, Any] = {
                "Bucket": self._bucket,
                "Prefix": prefix,
                "MaxKeys": max(1, min(page_size, 1000)),
            }
            if continuation:
                kwargs["ContinuationToken"] = continuation
            try:
                resp = client.list_objects_v2(**kwargs)
            except Exception as e:
                raise self._wrap_error("list_prefix", prefix, e) from e
            for c in resp.get("Contents", []) or []:
                yield S3Object(
                    key=c["Key"],
                    size=int(c.get("Size", 0)),
                    last_modified=c.get("LastModified"),
                )
            if not resp.get("IsTruncated"):
                return
            continuation = resp.get("NextContinuationToken")
            if not continuation:
                return

    def delete(self, key: str) -> None:
        """Delete a single object. Used by the monthly compactor to clean up
        staging files after a successful raw/ rollup."""
        try:
            self._get_client().delete_object(Bucket=self._bucket, Key=key)
        except Exception as e:
            raise self._wrap_error("delete", key, e) from e

    def delete_many(self, keys: Iterable[str]) -> int:
        """
        Batch-delete up to 1000 keys per S3 call. Returns the total number of
        keys deleted. Empty input is a no-op.
        """
        client = self._get_client()
        keys_list = [k for k in keys if k]
        if not keys_list:
            return 0
        deleted = 0
        # S3 DeleteObjects accepts up to 1000 keys per request.
        for i in range(0, len(keys_list), 1000):
            chunk = keys_list[i : i + 1000]
            try:
                resp = client.delete_objects(
                    Bucket=self._bucket,
                    Delete={"Objects": [{"Key": k} for k in chunk], "Quiet": True},
                )
            except Exception as e:
                raise self._wrap_error("delete_many", chunk[0], e) from e
            errors = resp.get("Errors") or []
            if errors:
                logger.warning(
                    "S3 delete_many: %d/%d failed (first=%r)",
                    len(errors), len(chunk), errors[0],
                )
            deleted += len(chunk) - len(errors)
        return deleted
