"""Sector rotation (RRG) API — the market-at-a-glance dashboard.

Returns each market group's RRG quadrant (Leading / Weakening / Improving /
Lagging) vs a benchmark (SPY), with the weekly tail + relative-strength line
for the frontend. Backed by ``app.services.sectors``. Phase 1 = 11 SPDR
sector ETFs. See docs/trading/sector_rotation.md.
"""
from __future__ import annotations

import logging

from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, Request

from app.config import settings
from app.services.sectors import RotationDashboard, SectorRotationService
from app.services.sectors import service as sectors_service
from app.services.sectors.personal_themes import PersonalThemesService
from app.services.sectors.schemas import (
    ThemeCreateRequest,
    ThemeMutationResponse,
    ThemeRecord,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def _session_user_id(request: Request) -> UUID | None:
    """The signed-in user's id, or None when auth is off (local single-operator
    dev has no per-user themes). With auth on the session gate has already
    required a session; a missing one here is a 401, never a silent fallback."""
    if not settings.auth_enabled:
        return None
    from app.api.auth_dependencies import get_principal_or_dev

    return get_principal_or_dev(request).user_id


@router.get("/sectors/rotation", response_model=RotationDashboard)
def get_sector_rotation(
    request: Request,
    benchmark: str = Query(
        default="", description="Benchmark symbol; defaults to the configured RRG benchmark (SPY)."
    ),
    tail_weeks: int = Query(
        default=0, ge=0, le=52,
        description="Weekly points in each sector's scatter tail; 0 ⇒ configured default.",
    ),
) -> RotationDashboard:
    """RRG dashboard for the sector universe vs `benchmark`, plus the signed-in
    user's own themes (flagged ``personal``) when auth is on."""
    bench = benchmark.strip().upper() or settings.rrg_benchmark
    weeks = tail_weeks or None
    user_id = _session_user_id(request)
    try:
        extra = []
        if user_id is not None:
            try:
                extra = PersonalThemesService.from_settings().groups_for(user_id, bench)
            except Exception:  # noqa: BLE001 — personal themes must never break the shared chart
                logger.exception("personal themes unavailable for user=%s; showing shared only", user_id)
        service = SectorRotationService.from_settings(benchmark=bench, extra_groups=extra)
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
