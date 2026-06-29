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
from app.services.sectors import service as sectors_service
from app.services.sectors.schemas import (
    ThemeCreateRequest,
    ThemeMutationResponse,
    ThemeRecord,
)

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


# ── Themes as data — runtime-editable thematic baskets ───────────────


@router.get("/sectors/themes", response_model=list[ThemeRecord])
def list_sector_themes() -> list[ThemeRecord]:
    """The thematic baskets currently defined (data-driven, from the store)."""
    try:
        return sectors_service.list_themes()
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.exception("list_sector_themes failed")
        raise HTTPException(status_code=500, detail=f"themes error: {exc}")


@router.post("/sectors/themes", response_model=ThemeMutationResponse, status_code=201)
async def create_sector_theme(req: ThemeCreateRequest) -> ThemeMutationResponse:
    """Create (or replace) a theme. New constituents are onboarded into the
    streaming universe in the background (membership + tip-fill + deep history)
    — nothing is ever removed from the universe."""
    try:
        return await sectors_service.create_theme(req)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.exception("create_sector_theme failed (%s)", req.name)
        raise HTTPException(status_code=500, detail=f"theme create error: {exc}")


@router.delete("/sectors/themes/{theme_id}", response_model=ThemeMutationResponse)
def delete_sector_theme(theme_id: str) -> ThemeMutationResponse:
    """Soft-delete a theme. Its constituents stay in the streaming universe
    (we never prune) — only the rotation grouping is removed."""
    try:
        return sectors_service.delete_theme(theme_id)
    except Exception as exc:  # noqa: BLE001 — boundary
        logger.exception("delete_sector_theme failed (%s)", theme_id)
        raise HTTPException(status_code=500, detail=f"theme delete error: {exc}")
