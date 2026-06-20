"""Authenticated customer boundary probes and current-user contract."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth_dependencies import get_identity_service, get_principal
from app.services.identity.service import IdentityService
from app.services.identity.schemas import CurrentUserResponse, Principal


router = APIRouter(prefix="/customer")


@router.get("/me", response_model=CurrentUserResponse)
async def current_user(
    principal: Principal = Depends(get_principal),
    identity_service: IdentityService = Depends(get_identity_service),
) -> CurrentUserResponse:
    current = await asyncio.to_thread(identity_service.get_current_user, principal)
    if current is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return current
