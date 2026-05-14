"""
S3 lake archive writer.

This is the canonical write path for ``s3://${STOCK_LAKE_BUCKET}/raw/``.
Every Parquet file in the lake is produced by this module so the layout
is deterministic and every successful write leaves an audit row in
``lake_archive_watermarks``.

Layered design::

    LakeArchiveWriter
       │
       ├──> S3LakeClient.put_parquet   (transport)
       └──> WatermarkRepo.record       (audit)

Both dependencies are injected via the constructor; production callers
use ``LakeArchiveWriter.from_settings()`` which wires them up from
``app.config``. Tests inject mocks and never touch S3 or ClickHouse.

The writer is intentionally **single-purpose**: given one
``(provider, kind, date, DataFrame)`` it produces one canonical Parquet
object and one watermark row. Anything plural — fan-out across sinks,
multi-day orchestration, backfill range bookkeeping — lives in callers
(see ``flatfiles_sinks.LakeSink`` and ``FlatFilesBackfillService``).

Canonical S3 layout
-------------------
``raw/provider={provider}/kind={kind}/year={YYYY}/date={YYYY-MM-DD}.parquet``

Hive-style partitioning so DuckDB / Athena / Spark can predicate-push on
``provider``, ``kind`` and ``year`` without listing the whole prefix.
``date`` is preserved as the leaf filename so a one-day rerun overwrites
exactly one object — no compaction surprises.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Optional

import pandas as pd

from app.db.lake_watermarks import (
    STATUS_ERROR,
    STATUS_OK,
    Watermark,
    WatermarkRepo,
    WatermarkRepoProtocol,
)
from app.services.s3_lake_client import S3LakeClient, S3LakeClientError

logger = logging.getLogger(__name__)


Kind = Literal["minute", "day"]
"""Aggregation grain. Maps 1:1 to the ClickHouse table name:
``minute`` -> ``ohlcv_1m``, ``day`` -> ``ohlcv_daily``."""


_TABLE_BY_KIND: dict[Kind, str] = {
    "minute": "ohlcv_1m",
    "day": "ohlcv_daily",
}


@dataclass(frozen=True, slots=True)
class LakeWriteResult:
    """Outcome of one ``LakeArchiveWriter.write_day`` call.

    Always returned (never ``None``) so callers can record telemetry
    even on a no-op skip. ``status`` is one of:

      - ``"ok"``      : Parquet written, watermark stamped
      - ``"skipped"`` : Idempotency hit; an ``ok`` watermark already
                        existed and ``force=False``
      - ``"error"``   : Write failed; watermark stamped with the failure
                        so ``lake_verify.sh gaps`` can surface the day
    """
    date: date
    kind: Kind
    provider: str
    s3_key: str
    bars_written: int
    bytes_written: int
    status: str  # "ok" | "skipped" | "error"
    error: Optional[str] = None


class LakeArchiveError(RuntimeError):
    """Raised by ``LakeArchiveWriter.write_day`` when the day fails in a
    way the caller probably wants to surface (e.g. S3 permission denied,
    not a transient that boto3 already retried). Watermark is *still*
    stamped with ``status='error'`` before the exception escapes so the
    audit trail reflects the failure."""


class LakeArchiveWriter:
    """
    Canonical writer for the S3 data lake.

    Construction:
        writer = LakeArchiveWriter(s3=..., watermarks=...)
        writer = LakeArchiveWriter.from_settings()  # production wiring

    Usage:
        result = await writer.write_day(
            df, file_date=date(2026, 5, 12),
            kind="minute", provider="polygon-flatfiles",
        )
        if result.status == "ok": ...

    Thread-safety: instance state is read-only after construction. Safe
    to share across asyncio tasks; the underlying S3 client / ClickHouse
    client both already handle concurrency per their own contracts.
    """

    DEFAULT_STAGE = "raw"
    KEY_TEMPLATE = (
        "{stage}/provider={provider}/kind={kind}/year={year:04d}/"
        "date={date}.parquet"
    )

    def __init__(
        self,
        *,
        s3: S3LakeClient,
        watermarks: WatermarkRepoProtocol,
        stage: str = DEFAULT_STAGE,
    ) -> None:
        if s3 is None:
            raise ValueError("LakeArchiveWriter: s3 client is required")
        if watermarks is None:
            raise ValueError("LakeArchiveWriter: watermarks repo is required")
        if not stage:
            raise ValueError("LakeArchiveWriter: stage cannot be empty")
        self._s3 = s3
        self._watermarks = watermarks
        self._stage = stage

    # ---------- factories ----------

    @classmethod
    def from_settings(cls) -> "LakeArchiveWriter":
        """Build the canonical instance from ``app.config.settings``.
        Raises ``ValueError`` (via ``S3LakeClient.from_settings``) if
        ``STOCK_LAKE_BUCKET`` is not set — caller decides whether that's
        fatal (CLI) or warn-and-skip (FastAPI lifespan)."""
        return cls(
            s3=S3LakeClient.from_settings(),
            watermarks=WatermarkRepo.from_clickhouse(),
        )

    # ---------- properties ----------

    @property
    def stage(self) -> str:
        return self._stage

    @property
    def bucket(self) -> str:
        return self._s3.bucket

    # ---------- key builder ----------

    def key_for(self, *, file_date: date, kind: Kind, provider: str) -> str:
        """
        Return the canonical S3 key for ``(provider, kind, date)``.

        Pure function — useful for verification scripts (``lake_verify.sh``)
        and the multi-sink fan-out in ``FlatFilesBackfillService`` so it
        can pre-print the destination for ``--dry-run`` output without
        touching S3.
        """
        _validate_kind(kind)
        _validate_provider(provider)
        return self.KEY_TEMPLATE.format(
            stage=self._stage,
            provider=provider,
            kind=kind,
            year=file_date.year,
            date=file_date.isoformat(),
        )

    # ---------- idempotency ----------

    async def already_archived(
        self,
        *,
        file_date: date,
        kind: Kind,
        provider: str,
    ) -> bool:
        """
        Return ``True`` when an ``ok``-status watermark already exists
        for the given day. Used by ``write_day(force=False)`` to skip
        re-archiving completed days during a resumed bulk seed.

        Note: a ``partial`` or ``error`` watermark returns ``False`` —
        re-running those is exactly what we want.
        """
        status = await self._watermarks.get_status(
            source=provider,
            table_name=_TABLE_BY_KIND[_validate_kind(kind)],
            period=file_date,
            stage=self._stage,
        )
        return status == STATUS_OK

    async def get_watermark(
        self,
        *,
        file_date: date,
        kind: Kind,
        provider: str,
    ) -> Optional[Watermark]:
        """Full audit row for the given day, or ``None``. Exposed so the
        CLI verifier can print the ``s3_key`` / ``bars_archived`` /
        ``archived_at`` triple without a second S3 ``HeadObject`` call."""
        return await self._watermarks.get(
            source=provider,
            table_name=_TABLE_BY_KIND[_validate_kind(kind)],
            period=file_date,
            stage=self._stage,
        )

    # ---------- write ----------

    async def write_day(
        self,
        df: pd.DataFrame,
        *,
        file_date: date,
        kind: Kind,
        provider: str,
        force: bool = False,
    ) -> LakeWriteResult:
        """
        Archive one calendar day's bars to the lake.

        Behaviour:
          1. If ``force=False`` and a successful watermark already
             exists, return immediately with ``status='skipped'`` —
             cheap (one ClickHouse SELECT) and lets a resumed seed
             skip thousands of already-complete days.
          2. Validate the input ``df`` (non-empty, has the canonical
             columns we expect — see ``_validate_frame``).
          3. PUT the Parquet at the canonical key.
          4. Stamp the watermark with ``status='ok'``, ``bars_archived``,
             and the ``s3_key`` used.

        On failure between (2) and (4): we stamp the watermark with
        ``status='error'`` (best-effort; if even the watermark write
        fails we log loudly and continue) so the day is visible to
        gap-fill tooling, then raise ``LakeArchiveError``.

        Returns:
            ``LakeWriteResult`` — never ``None``. Callers that prefer
            exception-based flow can check ``result.status``.
        """
        kind = _validate_kind(kind)
        _validate_provider(provider)
        table_name = _TABLE_BY_KIND[kind]
        key = self.key_for(file_date=file_date, kind=kind, provider=provider)

        # ---- 1. idempotency short-circuit ----
        if not force:
            if await self.already_archived(
                file_date=file_date, kind=kind, provider=provider,
            ):
                logger.info(
                    "lake_archive: skip %s/%s %s — watermark already=ok",
                    provider, kind, file_date,
                )
                return LakeWriteResult(
                    date=file_date, kind=kind, provider=provider,
                    s3_key=key, bars_written=0, bytes_written=0,
                    status="skipped",
                )

        # ---- 2. validate frame ----
        # An empty frame here means the caller upstream already filtered
        # everything out; that's "missing", not an error, but the lake
        # never writes empty objects. Treat as skipped without a
        # watermark (caller already classified the day correctly).
        if df is None or df.empty:
            logger.info(
                "lake_archive: skip %s/%s %s — empty frame",
                provider, kind, file_date,
            )
            return LakeWriteResult(
                date=file_date, kind=kind, provider=provider,
                s3_key=key, bars_written=0, bytes_written=0,
                status="skipped",
            )
        _validate_frame(df, kind=kind)
        bars = int(len(df))

        # ---- 3. S3 PUT (sync; offloaded to threadpool) ----
        # We pass the DataFrame straight through to ``put_parquet`` which
        # serialises in-process — boto3 then uses a multipart upload for
        # frames > 5 MB. Metadata is cheap provenance for ``aws s3api
        # head-object`` debugging.
        metadata = {
            "provider": provider,
            "kind": kind,
            "date": file_date.isoformat(),
            "bars": str(bars),
        }
        try:
            bytes_written = await self._put_parquet_async(key, df, metadata=metadata)
        except S3LakeClientError as e:
            await self._stamp_error(
                file_date=file_date, kind=kind, provider=provider,
                table_name=table_name, key=key, error=str(e),
            )
            raise LakeArchiveError(
                f"lake_archive: PUT {key} failed: {e}"
            ) from e

        # ---- 4. watermark stamp ----
        try:
            await self._watermarks.record(
                source=provider,
                table_name=table_name,
                period=file_date,
                stage=self._stage,
                bars_archived=bars,
                s3_key=key,
                status=STATUS_OK,
            )
        except Exception as e:
            # The S3 object IS written; failing the watermark is a soft
            # error. Log loudly so an operator notices, but don't raise:
            # the canonical data is in S3 and a re-run will re-stamp.
            logger.error(
                "lake_archive: PUT ok for %s but watermark stamp failed: %s",
                key, e,
            )
            return LakeWriteResult(
                date=file_date, kind=kind, provider=provider,
                s3_key=key, bars_written=bars, bytes_written=bytes_written,
                status="ok",  # data IS persisted; this is the truth
                error=f"watermark stamp failed: {e}",
            )

        logger.info(
            "lake_archive: wrote %s (%d bars, %d bytes)",
            key, bars, bytes_written,
        )
        return LakeWriteResult(
            date=file_date, kind=kind, provider=provider,
            s3_key=key, bars_written=bars, bytes_written=bytes_written,
            status="ok",
        )

    # ---------- internal ----------

    async def _put_parquet_async(
        self,
        key: str,
        df: pd.DataFrame,
        *,
        metadata: dict[str, str],
    ) -> int:
        # Wraps the sync boto3 call so the public API is async.
        import asyncio
        return await asyncio.to_thread(
            self._s3.put_parquet, key, df, metadata=metadata,
        )

    async def _stamp_error(
        self,
        *,
        file_date: date,
        kind: Kind,
        provider: str,
        table_name: str,
        key: str,
        error: str,
    ) -> None:
        """Best-effort error watermark. Never raises — if the audit-trail
        write itself fails, log it and let the caller raise the original
        error so we don't lose the root cause."""
        try:
            await self._watermarks.record(
                source=provider,
                table_name=table_name,
                period=file_date,
                stage=self._stage,
                bars_archived=0,
                s3_key=key,
                status=STATUS_ERROR,
                error=_truncate(error, 1024),
            )
        except Exception:
            logger.exception(
                "lake_archive: failed to stamp error watermark for %s", key,
            )


# ---------- validation helpers ----------


# Canonical frame columns by kind. Enforcing this here means downstream
# (DuckDB, Athena, future rehydration) gets a deterministic schema.
_CANONICAL_MINUTE_COLS = (
    "symbol", "timestamp",
    "open", "high", "low", "close",
    "volume", "vwap", "trade_count",
    "source",
)
_CANONICAL_DAILY_COLS = (
    "symbol", "timestamp",
    "open", "high", "low", "close",
    "volume",
    "source",
)


def _validate_kind(kind: Any) -> Kind:
    if kind not in ("minute", "day"):
        raise ValueError(f"unsupported kind: {kind!r}")
    return kind  # type: ignore[return-value]


def _validate_provider(provider: str) -> None:
    if not provider or not isinstance(provider, str):
        raise ValueError(f"provider must be a non-empty string, got {provider!r}")
    # Hive partition values are URL-ish; forbid characters that would
    # break the key template or DuckDB's partition parser.
    if "/" in provider or "=" in provider or " " in provider:
        raise ValueError(
            f"provider {provider!r} must not contain '/', '=' or whitespace"
        )


def _validate_frame(df: pd.DataFrame, *, kind: Kind) -> None:
    """Enforce the canonical column set. We're permissive about extras
    (the parquet keeps them) but strict about the required core so
    rehydration knows what to expect."""
    required = _CANONICAL_MINUTE_COLS if kind == "minute" else _CANONICAL_DAILY_COLS
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"lake_archive: frame missing required {kind} columns: {missing}; "
            f"have={list(df.columns)}"
        )


def _truncate(s: str, limit: int) -> str:
    """Bound error message length so a giant traceback doesn't blow up
    the LowCardinality(String) column."""
    s = s or ""
    if len(s) <= limit:
        return s
    return s[: limit - 3] + "..."


__all__ = [
    "Kind",
    "LakeArchiveError",
    "LakeArchiveWriter",
    "LakeWriteResult",
]
