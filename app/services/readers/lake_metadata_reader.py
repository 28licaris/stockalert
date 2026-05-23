"""
LakeMetadataReader — lake-wide metadata queries (CV29).

Snapshots, table-level stats. Not bar data — that's
`AdjustedOhlcvReader` (adjusted OHLCV) and `BronzeReader` (raw bars
+ legacy provider-keyed access).

Currently exposes:
  - `list_snapshots(tables=None, limit=20)` — recent Iceberg
    snapshots per equities table. Time-travel / audit / DR surface.

The four equities tables live behind `_EQUITIES_TABLES` so the
reader's default scope tracks CV1's schema; adding a fifth table
later means appending one entry here.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.services.equities.schemas import equities_table_id
from app.services.iceberg_catalog import get_catalog
from app.services.readers.schemas import (
    LakeSnapshot,
    LakeSnapshotsResponse,
)

logger = logging.getLogger(__name__)


# Default scope: every Iceberg table the v2 lake owns. Update when
# CV1's table list changes; the reader silently skips a name that
# Glue doesn't know about so a config drift is logged, not fatal.
_EQUITIES_TABLES = (
    "polygon_raw",
    "polygon_adjusted",
    "schwab_universe",
    "market_corp_actions",
)


class LakeMetadataReader:
    """Lake-wide metadata reader (CV29).

    Construct via `from_settings()` for production; pass `catalog`
    explicitly for tests.
    """

    def __init__(self, *, catalog=None) -> None:
        self._catalog = catalog

    @classmethod
    def from_settings(cls) -> "LakeMetadataReader":
        return cls()

    def _get_catalog(self):
        if self._catalog is None:
            self._catalog = get_catalog()
        return self._catalog

    def list_snapshots(
        self,
        *,
        tables: Optional[list[str]] = None,
        limit: int = 20,
    ) -> LakeSnapshotsResponse:
        """Recent Iceberg snapshots per equities table.

        `tables` accepts a subset of the v2 equities table short names
        (`polygon_raw`, `polygon_adjusted`, `schwab_universe`,
        `market_corp_actions`); omit for all four. Unknown names get
        a warning + are skipped.

        `limit` truncates each table's snapshot history (most recent
        first). Polygon flat-files nightly commits ~once/day; corp
        actions weekly; live writer every 5 min. The default 20 covers
        ~3 weeks for nightly tables and ~100 min for the live writer.

        Returns merged-and-sorted (committed_at DESC) snapshots
        across all requested tables. A table that fails to load is
        logged + excluded; the rest still flow through.

        Cost: one catalog.load_table per table + a metadata-only
        snapshots() iteration. Sub-second for the full default
        scope. Does NOT scan data files.
        """
        wanted = list(tables) if tables else list(_EQUITIES_TABLES)
        requested_fq: list[str] = []
        all_snaps: list[LakeSnapshot] = []

        cat = self._get_catalog()
        for short in wanted:
            if short not in _EQUITIES_TABLES:
                logger.warning(
                    "LakeMetadataReader.list_snapshots: unknown "
                    "table %r; expected one of %s",
                    short, _EQUITIES_TABLES,
                )
                continue
            fq = equities_table_id(short)
            requested_fq.append(fq)

            try:
                table = cat.load_table(fq)
            except Exception as e:
                logger.warning(
                    "LakeMetadataReader: %s not loadable (%s); "
                    "skipping for this query", fq, e,
                )
                continue

            try:
                snapshots = list(table.snapshots())
            except Exception as e:
                logger.warning(
                    "LakeMetadataReader: %s snapshots() failed (%s); "
                    "skipping", fq, e,
                )
                continue

            # Sort by committed_at DESC then truncate per-table so a
            # tiny limit doesn't lose recent snapshots from one table
            # to historical noise on another.
            snapshots.sort(
                key=lambda s: _committed_at_dt(s),
                reverse=True,
            )
            for snap in snapshots[: max(0, int(limit))]:
                all_snaps.append(_snapshot_to_model(fq, snap))

        # Final cross-table sort, DESC by committed_at, stable.
        all_snaps.sort(key=lambda s: s.committed_at, reverse=True)

        return LakeSnapshotsResponse(
            requested_tables=requested_fq,
            snapshots=all_snaps,
            count=len(all_snaps),
        )


def _committed_at_dt(snap) -> datetime:
    """Coerce Iceberg snapshot.timestamp_ms → UTC datetime."""
    return datetime.fromtimestamp(snap.timestamp_ms / 1000.0, tz=timezone.utc)


def _snapshot_to_model(table_fq: str, snap) -> LakeSnapshot:
    """Iceberg snapshot object → LakeSnapshot Pydantic model.

    Snapshot.summary is an Iceberg Summary; we read the canonical
    counter keys via the public dict-like access pattern. Both
    counter keys can be missing (depends on the writer), so default
    to None.
    """
    summary = getattr(snap, "summary", None)
    summary_dict = dict(summary) if summary else {}

    operation: Optional[str] = None
    if summary:
        # PyIceberg Summary exposes .operation or a string-keyed
        # `operation` value. Treat both shapes defensively.
        operation = getattr(summary, "operation", None) or summary_dict.get(
            "operation"
        )
        if operation is not None:
            operation = str(operation).lower()

    def _maybe_int(key: str) -> Optional[int]:
        v = summary_dict.get(key)
        if v is None:
            return None
        try:
            return int(v)
        except (TypeError, ValueError):
            return None

    return LakeSnapshot(
        table_name=table_fq,
        snapshot_id=int(snap.snapshot_id),
        committed_at=_committed_at_dt(snap),
        operation=operation,
        total_records=_maybe_int("total-records"),
        added_records=_maybe_int("added-records"),
        parent_snapshot_id=(
            int(snap.parent_snapshot_id)
            if getattr(snap, "parent_snapshot_id", None) is not None
            else None
        ),
    )


__all__ = ["LakeMetadataReader"]
