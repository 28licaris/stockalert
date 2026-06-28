"""Sector rotation (RRG) API — the market-at-a-glance dashboard.

Returns each market group's RRG quadrant (Leading / Weakening / Improving /
Lagging) vs a benchmark (SPY), with the weekly tail + relative-strength line
for the frontend. Backed by ``app.services.sectors``. Phase 1 = 11 SPDR
sector ETFs. See docs/sector_rotation_spec.md.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, Query

from app.config import settings
from app.services.sectors import RotationDashboard, SectorRotationService

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/sectors/rotation", response_model=RotationDashboard)
def get_sector_rotation(
    benchmark: str = Query(
        default="", description="Benchmark symbol; defaults to the configured RRG benchmark (SPY)."
    ),
    tail_weeks: int = Query(
        default=0, ge=0, le=52,
        description="Weekly points in each sector's scatter tail; 0 ⇒ configured default.",
    ),
) -> RotationDashboard:
    """RRG dashboard for the sector universe vs `benchmark`."""
    bench = benchmark.strip().upper() or settings.rrg_benchmark
    weeks = tail_weeks or None
    try:
        service = SectorRotationService.from_settings(benchmark=bench)
        return service.build_dashboard(tail_weeks=weeks)
    except Exception as exc:  # noqa: BLE001 — boundary; surface, don't mask
        logger.exception("get_sector_rotation failed (benchmark=%s)", bench)
        raise HTTPException(status_code=500, detail=f"sector rotation error: {exc}")
