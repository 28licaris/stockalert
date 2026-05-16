"""
LEGACY — pre-Iceberg "raw S3" sink.

Writes one Parquet object per (provider, kind, date) to
``s3://${STOCK_LAKE_BUCKET}/raw/provider=*/kind=*/year=YYYY/date=YYYY-MM-DD.parquet``
via ``LakeArchiveWriter``. Superseded by ``app.services.bronze.BronzeIcebergSink``
in Phase 1; retained for:

  - Re-running the legacy ``polygon_flatfiles_bulk_backfill.py`` and
    ``schwab_lake_backfill.py`` scripts that still write to the raw
    layout (used as a research/archive path, not production).
  - Tests of the old raw/ pipeline (``tests/test_flatfiles_sinks.py``,
    ``tests/test_lake_archive.py``).

Do not use this sink for any new work. The bronze Iceberg sink owns the
canonical S3 destination going forward. See data_platform_plan.md for
the full rationale (catalog metadata, time travel, schema evolution,
MERGE INTO).

Scheduled for Phase 6 removal once nothing depends on it.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date

import pandas as pd

from app.services.ingest.sinks import Kind, SinkResult
from app.services.legacy.lake_archive import LakeArchiveError, LakeArchiveWriter

logger = logging.getLogger(__name__)


class LakeSink:
    """
    Legacy S3 lake sink. Wraps ``LakeArchiveWriter.write_day`` to satisfy
    the ``Sink`` Protocol from ``app.services.ingest.sinks``.

    Replaced by ``app.services.bronze.BronzeIcebergSink`` (canonical
    bronze writer). This class is kept only for legacy scripts and
    tests; Phase 6 removes it.
    """
    name = "lake"

    def __init__(self, *, writer: LakeArchiveWriter, force: bool = False) -> None:
        if writer is None:
            raise ValueError("LakeSink: writer is required")
        self._writer = writer
        self._force = bool(force)

    @classmethod
    def from_settings(cls, *, force: bool = False) -> "LakeSink":
        return cls(writer=LakeArchiveWriter.from_settings(), force=force)

    @property
    def force(self) -> bool:
        return self._force

    @property
    def writer(self) -> LakeArchiveWriter:
        return self._writer

    async def write(
        self,
        df: pd.DataFrame,
        *,
        file_date: date,
        kind: Kind,
        provider: str,
    ) -> SinkResult:
        try:
            result = await self._writer.write_day(
                df, file_date=file_date, kind=kind, provider=provider,
                force=self._force,
            )
        except LakeArchiveError as e:
            return SinkResult(
                sink=self.name, status="error", bars_written=0,
                error=str(e),
            )
        return SinkResult(
            sink=self.name,
            status=result.status,
            bars_written=result.bars_written,
            error=result.error,
            metadata={
                "s3_key": result.s3_key,
                "bytes_written": result.bytes_written,
            },
        )


__all__ = ["LakeSink"]
