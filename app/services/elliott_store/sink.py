"""ElliottLabelSink — append-only writer for `elliott_wave_labels`.

Append-only, same idempotency model as the equities sink: the recompute job is
the upstream watermark (one run per trading day), so the hot path never does a
read-modify-write. Re-running a day appends a second row with the same
(symbol, interval, as_of_date) but a fresh `computed_at`; readers take the
latest `computed_at`, maintenance dedups via Athena (bronze-idempotency model).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import pyarrow as pa
from pyiceberg.table import Table

from app.services.elliott_store.schema import (
    ELLIOTT_WAVE_LABELS_ARROW,
    labeling_to_row,
)
from app.services.elliott_store.tables import ensure_elliott_wave_labels
from app.signals.elliott.schemas import WaveLabeling

logger = logging.getLogger(__name__)


class ElliottLabelSink:
    """Writes WaveLabelings to one namespace's `elliott_wave_labels` table."""

    def __init__(self, asset_class: str, table: Table) -> None:
        self.asset_class = asset_class
        self._table = table

    @classmethod
    def for_asset_class(cls, asset_class: str = "equity") -> "ElliottLabelSink":
        return cls(asset_class, ensure_elliott_wave_labels(asset_class))

    def write(self, labelings: list[WaveLabeling], *, git_sha: str = "") -> int:
        """Append one row per labeling. Returns rows written."""
        if not labelings:
            return 0
        computed_at = datetime.now(timezone.utc).replace(microsecond=0)
        rows = [labeling_to_row(lab, git_sha=git_sha, computed_at=computed_at)
                for lab in labelings]
        arrow = pa.Table.from_pylist(rows, schema=ELLIOTT_WAVE_LABELS_ARROW)
        self._table.append(arrow)
        logger.info("elliott_label_sink[%s]: appended %d rows", self.asset_class, len(rows))
        return len(rows)
