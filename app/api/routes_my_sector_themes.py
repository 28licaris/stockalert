"""Personal sector themes — `/api/v1/my/sector-themes`.

A customer's own baskets for the Sector Rotation page, stored in the identity
PostgreSQL and scoped to the signed-in user. They are NOT added to the shared
streaming universe (see ``app.services.sectors.personal_themes``).

Gated by ``get_principal_or_dev`` exactly like ``/my/watchlists``: a real
session when AUTH_ENABLED, the fixed local dev principal otherwise.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from uuid import UUID

from app.api.auth_dependencies import DEV_USER_EMAIL, get_principal_or_dev
from app.config import settings
from app.services.identity.schemas import Principal
from app.services.sectors.personal_themes import (
    PersonalTheme,
    PersonalThemeCreateRequest,
    PersonalThemeError,
    PersonalThemesService,
)

router = APIRouter(prefix="/api/v1/my/sector-themes", tags=["Sector Rotation"])


def _service() -> PersonalThemesService:
    return PersonalThemesService.from_settings()


def _user_id(principal: Principal) -> UUID:
    # With auth off the dev principal's user row must exist (FK target);
    # idempotent and cheap. With auth on the row exists by construction.
    if not settings.auth_enabled:
        from app.services.watchlists.repository import WatchlistsRepository

        WatchlistsRepository.from_settings().ensure_user(
            principal.user_id, DEV_USER_EMAIL, "Dev User"
        )
    return principal.user_id


@router.get("", response_model=list[PersonalTheme])
def list_my_themes(principal: Principal = Depends(get_principal_or_dev)) -> list[PersonalTheme]:
    return _service().list(_user_id(principal))


@router.post("", response_model=PersonalTheme, status_code=201)
def create_my_theme(
    body: PersonalThemeCreateRequest, principal: Principal = Depends(get_principal_or_dev)
) -> PersonalTheme:
    """Create, or replace the caller's theme of the same name."""
    try:
        return _service().create(_user_id(principal), body)
    except PersonalThemeError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@router.delete("/{slug}", status_code=204)
def delete_my_theme(slug: str, principal: Principal = Depends(get_principal_or_dev)) -> None:
    if not _service().delete(_user_id(principal), slug):
        raise HTTPException(status_code=404, detail="Theme not found.")
