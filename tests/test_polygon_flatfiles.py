"""
Unit tests for `app.providers.polygon_flatfiles`.

We never hit `files.massive.com` — every test injects a MagicMock as the
boto3 client so we can:
  - assert the exact S3 keys we'd request for a given date
  - feed back a real gzipped CSV body and verify the parser
  - simulate 404s (weekend/holiday) and confirm we degrade to an empty frame
  - paginate ``available_dates`` across multiple ``ContinuationToken`` pages
"""
from __future__ import annotations

import gzip
import io
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

import pandas as pd
import pytest

from app.providers.polygon_flatfiles import (
    FlatFileInfo,
    PolygonFlatFilesClient,
    PolygonFlatFilesError,
    _parse_date_from_key,
    iter_trading_days,
)


# ---------- fixtures ----------

def _csv_gz_bytes(rows: list[dict]) -> bytes:
    """Synthesize the gzipped CSV body Polygon would return for ``rows``."""
    df = pd.DataFrame(rows)
    csv_bytes = df.to_csv(index=False).encode("utf-8")
    return gzip.compress(csv_bytes)


def _make_client(**overrides) -> tuple[PolygonFlatFilesClient, MagicMock]:
    fake = MagicMock(name="s3_client")
    defaults = {
        "access_key_id": "AKIA-TEST",
        "secret_access_key": "secret-test",
        "client": fake,
    }
    defaults.update(overrides)
    return PolygonFlatFilesClient(**defaults), fake


# ---------- key resolution (pure) ----------

class TestKeyResolution:
    def test_minute_key_path(self):
        c, _ = _make_client()
        key = c.minute_aggs_key(date(2026, 5, 13))
        assert key == "us_stocks_sip/minute_aggs_v1/2026/05/2026-05-13.csv.gz"

    def test_day_key_path(self):
        c, _ = _make_client()
        key = c.day_aggs_key(date(2026, 5, 13))
        assert key == "us_stocks_sip/day_aggs_v1/2026/05/2026-05-13.csv.gz"

    def test_custom_prefix_is_honored(self):
        """Other asset classes share the layout under a different prefix."""
        c, _ = _make_client(stocks_prefix="us_options_opra")
        key = c.minute_aggs_key(date(2026, 1, 2))
        assert key.startswith("us_options_opra/minute_aggs_v1/2026/01/")


# ---------- downloads ----------

class TestDownloadMinuteAggs:
    def test_returns_dataframe_with_canonical_columns(self):
        c, fake = _make_client()
        ns_at_1430 = int(
            datetime(2026, 5, 13, 14, 30, tzinfo=timezone.utc).timestamp() * 1_000_000_000
        )
        ns_at_1431 = ns_at_1430 + 60 * 1_000_000_000
        body = _csv_gz_bytes([
            {"ticker": "AAPL", "volume": 1234.0, "open": 180.0, "close": 180.5,
             "high": 180.6, "low": 179.9, "window_start": ns_at_1430,
             "transactions": 50},
            {"ticker": "AAPL", "volume": 555.0, "open": 180.5, "close": 180.7,
             "high": 180.8, "low": 180.4, "window_start": ns_at_1431,
             "transactions": 30},
            {"ticker": "MSFT", "volume": 999.0, "open": 410.0, "close": 410.2,
             "high": 410.3, "low": 409.9, "window_start": ns_at_1430,
             "transactions": 40},
        ])
        fake.get_object.return_value = {"Body": io.BytesIO(body)}

        df = c.download_minute_aggs(date(2026, 5, 13))

        # Boto was called with the canonical key.
        fake.get_object.assert_called_once_with(
            Bucket="flatfiles",
            Key="us_stocks_sip/minute_aggs_v1/2026/05/2026-05-13.csv.gz",
        )
        # Canonical timestamp column was synthesised from window_start (ns).
        assert "timestamp" in df.columns
        assert df["timestamp"].dt.tz is not None
        assert len(df) == 3
        assert set(df["ticker"]) == {"AAPL", "MSFT"}

    def test_uppercases_tickers(self):
        """ClickHouse expects uppercase symbols. The CSV typically already
        ships them uppercase but we defensively normalize anyway."""
        c, fake = _make_client()
        body = _csv_gz_bytes([{
            "ticker": "aapl", "volume": 1, "open": 1, "close": 1,
            "high": 1, "low": 1,
            "window_start": int(
                datetime(2026, 5, 13, tzinfo=timezone.utc).timestamp() * 1e9
            ),
            "transactions": 1,
        }])
        fake.get_object.return_value = {"Body": io.BytesIO(body)}
        df = c.download_minute_aggs(date(2026, 5, 13))
        assert list(df["ticker"]) == ["AAPL"]

    def test_filters_to_requested_symbols(self):
        """A single Flat File contains ~11k tickers; callers typically want
        a much smaller universe (e.g. our seed-100)."""
        c, fake = _make_client()
        ns = int(datetime(2026, 5, 13, tzinfo=timezone.utc).timestamp() * 1e9)
        body = _csv_gz_bytes([
            {"ticker": "AAPL",  "volume": 1, "open": 1, "close": 1,
             "high": 1, "low": 1, "window_start": ns, "transactions": 1},
            {"ticker": "MSFT",  "volume": 1, "open": 1, "close": 1,
             "high": 1, "low": 1, "window_start": ns, "transactions": 1},
            {"ticker": "ZNGA",  "volume": 1, "open": 1, "close": 1,
             "high": 1, "low": 1, "window_start": ns, "transactions": 1},
        ])
        fake.get_object.return_value = {"Body": io.BytesIO(body)}

        df = c.download_minute_aggs(
            date(2026, 5, 13), symbols=["AAPL", "MSFT"],
        )
        assert set(df["ticker"]) == {"AAPL", "MSFT"}

    def test_filter_handles_lowercase_input(self):
        c, fake = _make_client()
        ns = int(datetime(2026, 5, 13, tzinfo=timezone.utc).timestamp() * 1e9)
        body = _csv_gz_bytes([
            {"ticker": "AAPL", "volume": 1, "open": 1, "close": 1,
             "high": 1, "low": 1, "window_start": ns, "transactions": 1},
            {"ticker": "MSFT", "volume": 1, "open": 1, "close": 1,
             "high": 1, "low": 1, "window_start": ns, "transactions": 1},
        ])
        fake.get_object.return_value = {"Body": io.BytesIO(body)}
        df = c.download_minute_aggs(date(2026, 5, 13), symbols=["aapl"])
        assert list(df["ticker"]) == ["AAPL"]

    def test_returns_empty_frame_when_file_does_not_exist(self):
        """Weekend / holiday: Polygon returns 404. Surface as empty DF."""
        c, fake = _make_client()
        err = Exception("missing")
        err.response = {  # type: ignore[attr-defined]
            "Error": {"Code": "NoSuchKey"},
            "ResponseMetadata": {"HTTPStatusCode": 404},
        }
        fake.get_object.side_effect = err

        df = c.download_minute_aggs(date(2026, 5, 16))  # Saturday
        assert df.empty

    def test_raises_on_real_errors(self):
        c, fake = _make_client()
        err = Exception("denied")
        err.response = {  # type: ignore[attr-defined]
            "Error": {"Code": "AccessDenied"},
            "ResponseMetadata": {"HTTPStatusCode": 403},
        }
        fake.get_object.side_effect = err
        with pytest.raises(PolygonFlatFilesError, match="get_object failed"):
            c.download_minute_aggs(date(2026, 5, 13))


class TestDownloadDayAggs:
    def test_uses_daily_key(self):
        c, fake = _make_client()
        ns = int(datetime(2026, 5, 13, tzinfo=timezone.utc).timestamp() * 1e9)
        body = _csv_gz_bytes([{
            "ticker": "AAPL", "volume": 1, "open": 1, "close": 1,
            "high": 1, "low": 1, "window_start": ns, "transactions": 1,
        }])
        fake.get_object.return_value = {"Body": io.BytesIO(body)}
        c.download_day_aggs(date(2026, 5, 13))
        fake.get_object.assert_called_once_with(
            Bucket="flatfiles",
            Key="us_stocks_sip/day_aggs_v1/2026/05/2026-05-13.csv.gz",
        )


# ---------- listing ----------

class TestAvailableDates:
    def _entry(self, key: str, size: int = 1024) -> dict:
        return {"Key": key, "Size": size, "LastModified": datetime.now(timezone.utc)}

    def test_lists_minute_files_in_range(self):
        c, fake = _make_client()
        fake.list_objects_v2.return_value = {
            "Contents": [
                self._entry("us_stocks_sip/minute_aggs_v1/2026/05/2026-05-13.csv.gz"),
                self._entry("us_stocks_sip/minute_aggs_v1/2026/05/2026-05-14.csv.gz"),
                # Weekend — Polygon wouldn't publish, but if it did the
                # date-window filter still keeps it in range.
                self._entry("us_stocks_sip/minute_aggs_v1/2026/05/2026-05-16.csv.gz"),
                # Out of requested range — must be filtered out.
                self._entry("us_stocks_sip/minute_aggs_v1/2026/05/2026-05-20.csv.gz"),
            ],
            "IsTruncated": False,
        }
        out = c.available_dates(date(2026, 5, 13), date(2026, 5, 16))
        assert [f.file_date for f in out] == [
            date(2026, 5, 13), date(2026, 5, 14), date(2026, 5, 16),
        ]
        assert all(isinstance(f, FlatFileInfo) for f in out)
        # list_objects_v2 was scoped to the 2026 prefix.
        kwargs = fake.list_objects_v2.call_args.kwargs
        assert kwargs["Prefix"] == "us_stocks_sip/minute_aggs_v1/2026/"

    def test_paginates_continuation_tokens(self):
        c, fake = _make_client()
        page1 = {
            "Contents": [
                self._entry("us_stocks_sip/minute_aggs_v1/2026/01/2026-01-02.csv.gz"),
            ],
            "IsTruncated": True,
            "NextContinuationToken": "next-1",
        }
        page2 = {
            "Contents": [
                self._entry("us_stocks_sip/minute_aggs_v1/2026/01/2026-01-05.csv.gz"),
            ],
            "IsTruncated": False,
        }
        fake.list_objects_v2.side_effect = [page1, page2]
        out = c.available_dates(date(2026, 1, 1), date(2026, 1, 31))
        assert [f.file_date for f in out] == [date(2026, 1, 2), date(2026, 1, 5)]
        # Continuation token was forwarded on the second call.
        assert fake.list_objects_v2.call_args_list[1].kwargs["ContinuationToken"] == "next-1"

    def test_spans_year_boundary(self):
        """Cross-year ranges should list each year's prefix exactly once."""
        c, fake = _make_client()
        fake.list_objects_v2.return_value = {"IsTruncated": False, "Contents": []}
        c.available_dates(date(2025, 12, 29), date(2026, 1, 5))
        # Two distinct year-scoped prefixes.
        prefixes = [
            call.kwargs["Prefix"] for call in fake.list_objects_v2.call_args_list
        ]
        assert "us_stocks_sip/minute_aggs_v1/2025/" in prefixes
        assert "us_stocks_sip/minute_aggs_v1/2026/" in prefixes

    def test_kind_must_be_minute_or_day(self):
        c, _ = _make_client()
        with pytest.raises(ValueError, match="unsupported kind"):
            c.available_dates(date(2026, 1, 1), date(2026, 1, 31), kind="trades")

    def test_returns_empty_when_end_before_start(self):
        c, _ = _make_client()
        assert c.available_dates(date(2026, 5, 14), date(2026, 5, 13)) == []


# ---------- helpers ----------

class TestParseDateFromKey:
    def test_extracts_yyyy_mm_dd(self):
        assert _parse_date_from_key(
            "us_stocks_sip/minute_aggs_v1/2026/05/2026-05-13.csv.gz"
        ) == date(2026, 5, 13)

    def test_returns_none_for_unknown_form(self):
        # Forward-compat: Polygon might add ``us_stocks_sip/foo/whatever.csv.gz``.
        assert _parse_date_from_key("us_stocks_sip/foo/whatever.csv.gz") is None
        # Wrong extension.
        assert _parse_date_from_key("us_stocks_sip/x/2026-05-13.parquet") is None


class TestIterTradingDays:
    def test_skips_weekends(self):
        # 2026-05-13 is a Wednesday.
        days = list(iter_trading_days(date(2026, 5, 13), date(2026, 5, 20)))
        # Wed Thu Fri (Sat Sun skipped) Mon Tue Wed
        assert days == [
            date(2026, 5, 13), date(2026, 5, 14), date(2026, 5, 15),
            date(2026, 5, 18), date(2026, 5, 19), date(2026, 5, 20),
        ]

    def test_handles_zero_length_range(self):
        assert list(iter_trading_days(date(2026, 5, 14), date(2026, 5, 13))) == []


class TestFromSettings:
    def test_factory_requires_credentials(self, monkeypatch):
        """Polygon Flat Files always require API-issued keys; missing keys
        should fail fast rather than producing confusing 403s downstream."""
        from app.config import settings as real_settings
        monkeypatch.setattr(real_settings, "polygon_s3_access_key_id", "")
        monkeypatch.setattr(real_settings, "polygon_s3_secret_access_key", "")
        with pytest.raises(ValueError, match="must both be set"):
            PolygonFlatFilesClient.from_settings()

    def test_factory_builds_with_credentials(self, monkeypatch):
        from app.config import settings as real_settings
        monkeypatch.setattr(real_settings, "polygon_s3_access_key_id", "AKIA-X")
        monkeypatch.setattr(real_settings, "polygon_s3_secret_access_key", "S")
        monkeypatch.setattr(real_settings, "polygon_s3_endpoint", "https://example.com")
        monkeypatch.setattr(real_settings, "polygon_s3_bucket", "mybucket")
        c = PolygonFlatFilesClient.from_settings()
        assert c.bucket == "mybucket"
