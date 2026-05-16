"""
Unit tests for `app.services.s3_lake_client`.

These tests **never touch real S3**. Every test injects a ``MagicMock`` as the
boto3 client so we can:

  - assert the exact arguments boto3 was called with (Bucket, Key, Body, etc.)
  - simulate error responses (NoSuchKey, AccessDenied) and verify error
    handling
  - run in CI without AWS credentials

Coverage targets:
  - ``put_parquet`` round-trips a DataFrame to bytes correctly and rejects
    empty inputs
  - ``head`` returns ``None`` for 404 and raises for everything else
  - ``list_prefix`` paginates correctly across multiple pages
  - ``delete_many`` chunks at the 1000-key boundary
  - factories: ``from_settings``, default-credential fallback
"""
from __future__ import annotations

import io
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
import pyarrow.parquet as pq
import pytest

from app.services.legacy.s3_lake_client import (
    S3LakeClient,
    S3LakeClientError,
    S3Object,
)


def _make_client(**overrides) -> tuple[S3LakeClient, MagicMock]:
    """Build an ``S3LakeClient`` wired to a MagicMock S3 client. Returns the
    pair so each test can assert call args without monkey-patching."""
    fake = MagicMock(name="s3_client")
    defaults = {
        "bucket": "stock-lake-test",
        "region": "us-east-1",
        "client": fake,
    }
    defaults.update(overrides)
    return S3LakeClient(**defaults), fake


class TestConstruction:
    def test_requires_bucket(self):
        with pytest.raises(ValueError, match="bucket is required"):
            S3LakeClient(bucket="", client=MagicMock())

    def test_defaults_to_us_east_1(self):
        c, _ = _make_client(region="")
        assert c.region == "us-east-1"

    def test_exposes_bucket_property(self):
        c, _ = _make_client(bucket="mybucket")
        assert c.bucket == "mybucket"


class TestPutParquet:
    def test_writes_dataframe_as_parquet(self):
        c, fake = _make_client()
        df = pd.DataFrame(
            {"symbol": ["AAPL", "AAPL"], "close": [100.0, 101.0]},
            index=pd.date_range("2026-05-13", periods=2, freq="1min", tz="UTC"),
        )
        size = c.put_parquet("raw/test.parquet", df)

        assert size > 0
        # put_object called exactly once with the right Bucket/Key.
        fake.put_object.assert_called_once()
        kwargs = fake.put_object.call_args.kwargs
        assert kwargs["Bucket"] == "stock-lake-test"
        assert kwargs["Key"] == "raw/test.parquet"
        assert kwargs["ContentType"] == "application/octet-stream"

        # Body is real Parquet bytes that round-trip to a DataFrame.
        body = kwargs["Body"]
        round_trip = pq.read_table(io.BytesIO(body)).to_pandas()
        assert len(round_trip) == 2
        assert list(round_trip["symbol"]) == ["AAPL", "AAPL"]

    def test_rejects_empty_dataframe(self):
        """Empty Parquet files complicate compaction queries. Reject early."""
        c, fake = _make_client()
        with pytest.raises(ValueError, match="empty DataFrame"):
            c.put_parquet("raw/empty.parquet", pd.DataFrame())
        fake.put_object.assert_not_called()

    def test_rejects_none_dataframe(self):
        c, fake = _make_client()
        with pytest.raises(ValueError, match="empty DataFrame"):
            c.put_parquet("raw/none.parquet", None)  # type: ignore[arg-type]
        fake.put_object.assert_not_called()

    def test_metadata_is_forwarded(self):
        """S3 object metadata (x-amz-meta-*) is the right place for provenance."""
        c, fake = _make_client()
        df = pd.DataFrame({"x": [1]})
        c.put_parquet("k.parquet", df, metadata={"source": "polygon", "bars": 1})

        kwargs = fake.put_object.call_args.kwargs
        assert kwargs["Metadata"] == {"source": "polygon", "bars": "1"}

    def test_wraps_boto_errors(self):
        c, fake = _make_client()
        fake.put_object.side_effect = RuntimeError("boom")
        with pytest.raises(S3LakeClientError, match="put_parquet failed"):
            c.put_parquet("k.parquet", pd.DataFrame({"x": [1]}))


class TestPutBytes:
    def test_uploads_raw_bytes(self):
        c, fake = _make_client()
        n = c.put_bytes("file.bin", b"hello world")
        assert n == 11
        kwargs = fake.put_object.call_args.kwargs
        assert kwargs["Body"] == b"hello world"
        assert kwargs["ContentType"] == "application/octet-stream"

    def test_respects_content_type_override(self):
        c, fake = _make_client()
        c.put_bytes("k.json", b"{}", content_type="application/json")
        assert fake.put_object.call_args.kwargs["ContentType"] == "application/json"


class TestHead:
    def test_returns_metadata_when_object_exists(self):
        c, fake = _make_client()
        fake.head_object.return_value = {"ContentLength": 42}
        out = c.head("k.parquet")
        assert out == {"ContentLength": 42}
        fake.head_object.assert_called_once_with(
            Bucket="stock-lake-test", Key="k.parquet",
        )

    def test_returns_none_for_404(self):
        """boto3 raises ClientError for missing keys; we normalize to None
        so callers can `if client.head(k): ...`. Duck-type the error shape."""
        c, fake = _make_client()
        err = Exception("missing")
        # Imitate the botocore ClientError shape.
        err.response = {  # type: ignore[attr-defined]
            "Error": {"Code": "404", "Message": "Not Found"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        }
        fake.head_object.side_effect = err
        assert c.head("missing") is None

    def test_raises_on_access_denied(self):
        """A permission error must NOT be silently swallowed as "missing"."""
        c, fake = _make_client()
        err = Exception("denied")
        err.response = {  # type: ignore[attr-defined]
            "Error": {"Code": "AccessDenied", "Message": "Forbidden"},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        }
        fake.head_object.side_effect = err
        with pytest.raises(S3LakeClientError, match="head failed"):
            c.head("k")

    def test_exists_helper(self):
        c, fake = _make_client()
        fake.head_object.return_value = {"ContentLength": 1}
        assert c.exists("k") is True

        err = Exception()
        err.response = {  # type: ignore[attr-defined]
            "Error": {"Code": "NoSuchKey"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        }
        fake.head_object.side_effect = err
        assert c.exists("k") is False


class TestGetBytes:
    def test_reads_object_body(self):
        c, fake = _make_client()
        body = io.BytesIO(b"hello")
        fake.get_object.return_value = {"Body": body}
        out = c.get_bytes("k")
        assert out == b"hello"

    def test_wraps_errors(self):
        c, fake = _make_client()
        fake.get_object.side_effect = RuntimeError("oops")
        with pytest.raises(S3LakeClientError, match="get_bytes failed"):
            c.get_bytes("k")


class TestListPrefix:
    def _list_page(self, keys: list[str], *, truncated: bool = False,
                   token: str | None = None) -> dict:
        now = datetime(2026, 5, 13, tzinfo=timezone.utc)
        contents = [
            {"Key": k, "Size": 100, "LastModified": now} for k in keys
        ]
        out: dict = {"Contents": contents, "IsTruncated": truncated}
        if token is not None:
            out["NextContinuationToken"] = token
        return out

    def test_single_page(self):
        c, fake = _make_client()
        fake.list_objects_v2.return_value = self._list_page(["a", "b", "c"])
        items = list(c.list_prefix("raw/"))
        assert [i.key for i in items] == ["a", "b", "c"]
        assert all(isinstance(i, S3Object) for i in items)
        # Only one boto call for a non-truncated page.
        assert fake.list_objects_v2.call_count == 1

    def test_paginates_across_continuation_tokens(self):
        c, fake = _make_client()
        fake.list_objects_v2.side_effect = [
            self._list_page(["a", "b"], truncated=True, token="next-1"),
            self._list_page(["c", "d"], truncated=True, token="next-2"),
            self._list_page(["e"], truncated=False),
        ]
        items = list(c.list_prefix("raw/"))
        assert [i.key for i in items] == ["a", "b", "c", "d", "e"]
        # Each subsequent call should carry the continuation token.
        calls = fake.list_objects_v2.call_args_list
        assert "ContinuationToken" not in calls[0].kwargs
        assert calls[1].kwargs["ContinuationToken"] == "next-1"
        assert calls[2].kwargs["ContinuationToken"] == "next-2"

    def test_handles_empty_prefix(self):
        c, fake = _make_client()
        fake.list_objects_v2.return_value = {"IsTruncated": False}
        assert list(c.list_prefix("nope/")) == []


class TestDelete:
    def test_delete_single_key(self):
        c, fake = _make_client()
        c.delete("raw/x.parquet")
        fake.delete_object.assert_called_once_with(
            Bucket="stock-lake-test", Key="raw/x.parquet",
        )

    def test_delete_many_empty_is_noop(self):
        c, fake = _make_client()
        assert c.delete_many([]) == 0
        fake.delete_objects.assert_not_called()

    def test_delete_many_chunks_at_1000(self):
        """S3 DeleteObjects caps at 1000 keys per call; we should chunk."""
        c, fake = _make_client()
        fake.delete_objects.return_value = {}
        keys = [f"k{i}" for i in range(2500)]
        deleted = c.delete_many(keys)
        assert deleted == 2500
        assert fake.delete_objects.call_count == 3  # 1000 + 1000 + 500

        chunk_sizes = [
            len(call.kwargs["Delete"]["Objects"])
            for call in fake.delete_objects.call_args_list
        ]
        assert chunk_sizes == [1000, 1000, 500]

    def test_delete_many_counts_errors(self):
        """When S3 reports per-key errors, the count should reflect the
        successful deletes only."""
        c, fake = _make_client()
        fake.delete_objects.return_value = {
            "Errors": [{"Key": "k3", "Code": "AccessDenied"}],
        }
        deleted = c.delete_many(["k1", "k2", "k3"])
        assert deleted == 2  # 3 attempted - 1 errored


class TestFromSettings:
    def test_factory_pulls_from_settings(self, monkeypatch):
        """`from_settings()` is the single canonical bridge to the global
        config. Verify it forwards every field."""
        from app.config import settings as real_settings
        monkeypatch.setattr(real_settings, "stock_lake_bucket", "factory-bucket")
        monkeypatch.setattr(real_settings, "stock_lake_region", "us-west-2")
        monkeypatch.setattr(real_settings, "aws_access_key_id", "AKIA-FACTORY")
        monkeypatch.setattr(real_settings, "aws_secret_access_key", "secret-factory")
        monkeypatch.setattr(real_settings, "aws_session_token", "")

        c = S3LakeClient.from_settings()
        assert c.bucket == "factory-bucket"
        assert c.region == "us-west-2"

    def test_factory_raises_when_bucket_missing(self, monkeypatch):
        from app.config import settings as real_settings
        monkeypatch.setattr(real_settings, "stock_lake_bucket", "")
        with pytest.raises(ValueError, match="bucket is required"):
            S3LakeClient.from_settings()
