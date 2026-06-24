"""Elliott Wave label store — the `elliott_wave_labels` Iceberg table + the
nightly recompute job that fills it.

Public surface:
    ensure_elliott_wave_labels(asset_class)   — idempotent table creation
    ElliottLabelSink.for_asset_class(...)      — append-only writer
    compute_labeling / recompute_universe      — the labeling + job body
    run_elliott_recompute_loop                 — the nightly background loop
"""
from __future__ import annotations

from app.services.elliott_store.recompute import (
    compute_labeling,
    recompute_symbol,
    recompute_universe,
    run_elliott_recompute_loop,
    run_now_recompute,
)
from app.services.elliott_store.sink import ElliottLabelSink
from app.services.elliott_store.tables import ensure_elliott_wave_labels

__all__ = [
    "ensure_elliott_wave_labels",
    "ElliottLabelSink",
    "compute_labeling",
    "recompute_symbol",
    "recompute_universe",
    "run_now_recompute",
    "run_elliott_recompute_loop",
]
