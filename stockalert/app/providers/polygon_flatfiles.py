"""
Read-only S3 client for Polygon (Massive) **Flat Files**.

Polygon publishes bulk historical aggregates as gzipped CSVs in an
S3-compatible bucket at ``https://files.massive.com``. One file per
trading day, with **every US equity ticker** in that day's bar — so a
single download fetches ~5–10 MB and yields the entire market for the day.

Layout (US equities, SIP feed):

  ``us_stocks_sip/minute_aggs_v1/<YYYY>/<MM>/<YYYY-MM-DD>.csv.gz``
  ``us_stocks_sip/day_aggs_v1/<YYYY>/<YYYY-MM-DD>.csv.gz``

CSV columns: ``ticker, volume, open, close, high, low, window_start,
transactions``. ``window_start`` is a Unix nanosecond timestamp in UTC.

This module is **transport-only**: it downloads, parses, and returns a
DataFrame. It does NOT touch ClickHouse, ``settings`` (except for the
``from_settings()`` factory), the backfill scheduler, or the S3 lake. That
separation is deliberate — the same client is used by:

  * ``FlatFilesBackfillService`` (in-app daily/weekly bulk ingestion)
  * ``scripts/polygon_flatfiles_bulk_backfill.py`` (one-off CLI runs)
  * future analytics jobs that want raw daily files directly

Boto3 is initialised lazily so importing this module costs nothing.
"""
from __future__ import annotations

import gzip
import io
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# Polygon's US-equities flat-files prefix. Other asset classes
# (us_options_opra, us_indices, global_crypto, global_forex) follow the
# same shape; expose ``stocks_prefix`` so callers can swap if needed.
DEFAULT_STOCKS_PREFIX = "us_stocks_sip"


class PolygonFlatFilesError(RuntimeError):
    """Raised on download / parse failures so callers can catch a single type."""


@dataclass(frozen=True, slots=True)
class FlatFileInfo:
    """Lightweight view of one S3 listing entry. Returned by
    ``available_dates`` for callers that want to enumerate before downloading."""
    key: str
    file_date: date
    size: int


class PolygonFlatFilesClient:
    """
    Boto3-backed reader for Polygon's S3-compatible flat-files bucket.

    Construction is explicit; ``from_settings()`` builds the canonical
    instance from ``app.config`` for app-side callers, and tests inject a
    mock ``client=`` so they never need network access.
    """

    DEFAULT_ENDPOINT = "https://files.massive.com"
    DEFAULT_BUCKET = "flatfiles"

    # CSV column dtypes. Polygon's flat files ship some volumes as fractional
    # floats (Polygon aggregates fractional cents during pre/post-market), so
    # volume / transactions must be parsed as floats then cast where needed.
    _CSV_DTYPES: dict[str, Any] = {
        "ticker":       "string",
        "volume":       "float64",
        "open":         "float64",
        "close":        "float64",
        "high":         "float64",
        "low":          "float64",
        "window_start": "int64",
        "transactions": "Int64",  # nullable; Polygon sometimes omits
    }

    def __init__(
        self,
        *,
        access_key_id: Optional[str] = None,
        secret_access_key: Optional[str] = None,
        endpoint_url: str = DEFAULT_ENDPOINT,
        bucket: str = DEFAULT_BUCKET,
        stocks_prefix: str = DEFAULT_STOCKS_PREFIX,
        client: Any | None = None,
    ) -> None:
        self._access_key_id = (access_key_id or "").strip() or None
        self._secret_access_key = (secret_access_key or "").strip() or None
        self._endpoint_url = endpoint_url
        self._bucket = bucket
        self._stocks_prefix = stocks_prefix.strip("/")
        # Lazy boto3 client (tests inject a mock).
        self._client = client

    # ---------- factory ----------

    @classmethod
    def from_settings(cls) -> "PolygonFlatFilesClient":
        """Build from ``app.config.settings``. Raises ``ValueError`` if
        credentials aren't present — Polygon Flat Files always require an
        API-issued access key, so silent fall-through would just fail later
        with a confusing 403."""
        from app.config import settings
        if not (settings.polygon_s3_access_key_id and settings.polygon_s3_secret_access_key):
            raise ValueError(
                "PolygonFlatFilesClient: POLYGON_S3_ACCESS_KEY_ID and "
                "POLYGON_S3_SECRET_ACCESS_KEY must both be set in .env"
            )
        return cls(
            access_key_id=settings.polygon_s3_access_key_id,
            secret_access_key=settings.polygon_s3_secret_access_key,
            endpoint_url=settings.polygon_s3_endpoint or cls.DEFAULT_ENDPOINT,
            bucket=settings.polygon_s3_bucket or cls.DEFAULT_BUCKET,
        )

    # ---------- properties ----------

    @property
    def bucket(self) -> str:
        return self._bucket

    @property
    def stocks_prefix(self) -> str:
        return self._stocks_prefix

    # ---------- key resolution ----------

    def minute_aggs_key(self, d: date) -> str:
        """Return the S3 key for the 1-minute aggregates file on date ``d``.
        Pure function — never makes a network call."""
        return (
            f"{self._stocks_prefix}/minute_aggs_v1/{d.year:04d}/"
            f"{d.month:02d}/{d:%Y-%m-%d}.csv.gz"
        )

    def day_aggs_key(self, d: date) -> str:
        """S3 key for the daily aggregates file on date ``d``.

        Polygon's day-aggs layout matches minute-aggs: ``YYYY/MM/YYYY-MM-DD.csv.gz``.
        Earlier docs implied a flat ``YYYY/YYYY-MM-DD.csv.gz`` shape, but live
        listings confirm the month subdirectory is present.
        """
        return (
            f"{self._stocks_prefix}/day_aggs_v1/{d.year:04d}/"
            f"{d.month:02d}/{d:%Y-%m-%d}.csv.gz"
        )

    # ---------- internal ----------

    def _get_client(self) -> Any:
        if self._client is None:
            import boto3
            kwargs: dict[str, Any] = {
                "endpoint_url": self._endpoint_url,
                # Polygon's bucket is in us-east-1; region is informational
                # for boto3 signing but harmless to set.
                "region_name": "us-east-1",
            }
            if self._access_key_id and self._secret_access_key:
                kwargs["aws_access_key_id"] = self._access_key_id
                kwargs["aws_secret_access_key"] = self._secret_access_key
            self._client = boto3.client("s3", **kwargs)
        return self._client

    def _read_csv_gz(self, body: bytes) -> pd.DataFrame:
        """Decompress a gzipped CSV body and parse into a DataFrame. Adds the
        canonical ``timestamp`` (UTC datetime) column derived from
        ``window_start`` nanoseconds, plus uppercases the ticker for
        ClickHouse compatibility."""
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as gz:
            df = pd.read_csv(gz, dtype=self._CSV_DTYPES)
        if df.empty:
            return df
        df["ticker"] = df["ticker"].astype("string").str.upper()
        # window_start is nanoseconds since Unix epoch, UTC.
        df["timestamp"] = pd.to_datetime(df["window_start"], unit="ns", utc=True)
        return df

    def _get_object_bytes(self, key: str) -> bytes:
        client = self._get_client()
        try:
            resp = client.get_object(Bucket=self._bucket, Key=key)
        except Exception as e:
            raise PolygonFlatFilesError(
                f"Polygon Flat Files get_object failed for s3://{self._bucket}/{key}: {e}"
            ) from e
        body = resp["Body"]
        try:
            return body.read()
        finally:
            close = getattr(body, "close", None)
            if callable(close):
                close()

    # ---------- public API ----------

    def download_minute_aggs(
        self, d: date, *, symbols: Optional[Iterable[str]] = None,
    ) -> pd.DataFrame:
        """
        Download the 1-minute aggregates file for ``d`` and return a DataFrame
        keyed by ticker + timestamp. Optionally filter to a subset of symbols
        before returning so callers don't have to materialize the full
        ~11k-ticker frame when they only need a small universe.

        Returns an EMPTY DataFrame if the requested date has no file (weekend
        or holiday). Raises ``PolygonFlatFilesError`` on any other failure.
        """
        return self._download_aggs(
            key=self.minute_aggs_key(d),
            d=d,
            symbols=symbols,
            kind="minute",
        )

    def download_day_aggs(
        self, d: date, *, symbols: Optional[Iterable[str]] = None,
    ) -> pd.DataFrame:
        """Daily-aggregate equivalent of ``download_minute_aggs``."""
        return self._download_aggs(
            key=self.day_aggs_key(d),
            d=d,
            symbols=symbols,
            kind="day",
        )

    def _download_aggs(
        self,
        *,
        key: str,
        d: date,
        symbols: Optional[Iterable[str]],
        kind: str,
    ) -> pd.DataFrame:
        try:
            body = self._get_object_bytes(key)
        except PolygonFlatFilesError as e:
            # 404 / NoSuchKey -> file just doesn't exist (weekend/holiday).
            # Anything else is a real failure (permissions, network).
            response = getattr(e.__cause__, "response", None)
            code = ""
            http_status: Optional[int] = None
            if isinstance(response, dict):
                code = response.get("Error", {}).get("Code", "") or ""
                http_status = response.get("ResponseMetadata", {}).get(
                    "HTTPStatusCode"
                )
            if str(code) in {"404", "NoSuchKey", "NotFound"} or http_status == 404:
                logger.debug(
                    "Polygon Flat Files: no %s file for %s (likely market closed)",
                    kind, d.isoformat(),
                )
                return pd.DataFrame()
            raise

        df = self._read_csv_gz(body)
        if df.empty:
            return df
        if symbols is not None:
            wanted = {s.strip().upper() for s in symbols if s and s.strip()}
            if wanted:
                df = df[df["ticker"].isin(wanted)].copy()
        return df

    def available_dates(
        self,
        start: date,
        end: date,
        *,
        kind: str = "minute",
    ) -> list[FlatFileInfo]:
        """
        List the trading days actually available in ``[start, end]`` for the
        given aggregate ``kind`` (``"minute"`` or ``"day"``). Drives the
        backfill scheduler so it doesn't waste 404s on weekends/holidays.

        Returns a chronologically sorted list of ``FlatFileInfo``.
        """
        if kind not in ("minute", "day"):
            raise ValueError(f"unsupported kind: {kind!r}")
        if end < start:
            return []

        client = self._get_client()
        out: list[FlatFileInfo] = []
        # Listing is cheap (sub-second for a year of US equities) but we
        # group by year so we don't accidentally pull the whole bucket.
        year_iter = range(start.year, end.year + 1)
        for year in year_iter:
            prefix = (
                f"{self._stocks_prefix}/minute_aggs_v1/{year:04d}/"
                if kind == "minute"
                else f"{self._stocks_prefix}/day_aggs_v1/{year:04d}/"
            )
            continuation: Optional[str] = None
            while True:
                kwargs: dict[str, Any] = {
                    "Bucket": self._bucket,
                    "Prefix": prefix,
                    "MaxKeys": 1000,
                }
                if continuation:
                    kwargs["ContinuationToken"] = continuation
                try:
                    resp = client.list_objects_v2(**kwargs)
                except Exception as e:
                    raise PolygonFlatFilesError(
                        f"Polygon Flat Files list_objects_v2 failed under {prefix}: {e}"
                    ) from e
                for c in resp.get("Contents", []) or []:
                    file_date = _parse_date_from_key(c["Key"])
                    if file_date is None:
                        continue
                    if file_date < start or file_date > end:
                        continue
                    out.append(FlatFileInfo(
                        key=c["Key"],
                        file_date=file_date,
                        size=int(c.get("Size", 0)),
                    ))
                if not resp.get("IsTruncated"):
                    break
                continuation = resp.get("NextContinuationToken")
                if not continuation:
                    break
        out.sort(key=lambda f: f.file_date)
        return out


def _parse_date_from_key(key: str) -> Optional[date]:
    """Extract the ``YYYY-MM-DD`` date from a flat-files key. Returns None
    if the key doesn't end in the expected form (defensive against Polygon
    adding new file types under the same prefix in the future)."""
    # All flat-files filenames look like ``.../YYYY-MM-DD.csv.gz``.
    if not key.endswith(".csv.gz"):
        return None
    name = key.rsplit("/", 1)[-1]
    stem = name[:-len(".csv.gz")]
    try:
        return datetime.strptime(stem, "%Y-%m-%d").date()
    except ValueError:
        return None


def iter_trading_days(start: date, end: date) -> Iterable[date]:
    """
    Yield every weekday between ``start`` and ``end`` inclusive. Holiday
    detection is deferred to the actual S3 listing (``available_dates``)
    so we don't ship a fragile holiday calendar.
    """
    d = start
    one = timedelta(days=1)
    while d <= end:
        if d.weekday() < 5:  # 0=Mon..4=Fri
            yield d
        d += one


# Re-export for callers that just need timezone-aware utility imports.
__all__ = [
    "DEFAULT_STOCKS_PREFIX",
    "FlatFileInfo",
    "PolygonFlatFilesClient",
    "PolygonFlatFilesError",
    "iter_trading_days",
]

# Silence "unused" warning on `timezone` import while keeping the public
# contract that this module is `datetime`-aware (callers building dates
# typically need timezone import alongside).
_ = timezone
