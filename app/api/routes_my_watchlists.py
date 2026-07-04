"""Per-user watchlists — `/api/v1/my/watchlists`.

Every route is scoped to the authenticated Principal (or the local dev
principal when AUTH_ENABLED=false — see get_principal_or_dev). Each
watched symbol carries a pretend position (default 100 shares stamped
at the add-time price); returns are computed at read time.
"""
from __future__ import annotations

from functools import lru_cache

from fastapi import APIRouter, Depends, HTTPException, status

from app.api.auth_dependencies import DEV_USER_EMAIL, get_principal_or_dev
from app.services.identity.schemas import Principal
from app.services.watchlists.schemas import (
    MemberAdd,
    WatchlistCreate,
    WatchlistDetail,
    WatchlistOut,
)
from app.services.watchlists.service import WatchlistsService

router = APIRouter(prefix="/api/v1/my/watchlists", tags=["my-watchlists"])


@lru_cache(maxsize=1)
def _service() -> WatchlistsService:
    return WatchlistsService.from_settings()


def _user_id(principal: Principal):
    # Dev principal's user row must exist (FK target); idempotent, cheap.
    svc = _service()
    svc.ensure_user(principal.user_id, DEV_USER_EMAIL, "Dev User")
    return principal.user_id


@router.get("", response_model=list[WatchlistOut])
def list_watchlists(principal: Principal = Depends(get_principal_or_dev)):
    return _service().list(_user_id(principal))


@router.post("", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
def create_watchlist(body: WatchlistCreate, principal: Principal = Depends(get_principal_or_dev)):
    try:
        return _service().create(_user_id(principal), body.name, body.description)
    except Exception as exc:  # unique-violation -> 409
        raise HTTPException(status_code=409, detail=f"watchlist exists or invalid: {exc}") from exc


@router.get("/{name}", response_model=WatchlistDetail)
def get_watchlist(name: str, principal: Principal = Depends(get_principal_or_dev)):
    try:
        return _service().detail(_user_id(principal), name)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"watchlist {name!r} not found") from None


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_watchlist(name: str, principal: Principal = Depends(get_principal_or_dev)):
    if not _service().delete(_user_id(principal), name):
        raise HTTPException(status_code=404, detail=f"watchlist {name!r} not found")


@router.post("/{name}/members", response_model=WatchlistDetail)
def add_members(name: str, body: MemberAdd, principal: Principal = Depends(get_principal_or_dev)):
    try:
        return _service().add_symbols(_user_id(principal), name, body.symbols, body.quantity)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"watchlist {name!r} not found") from None


@router.delete("/{name}/members/{symbol}", response_model=WatchlistDetail)
def remove_member(name: str, symbol: str, principal: Principal = Depends(get_principal_or_dev)):
    uid = _user_id(principal)
    if not _service().remove_symbol(uid, name, symbol):
        raise HTTPException(status_code=404, detail=f"{symbol} not in {name!r}")
    return _service().detail(uid, name)
