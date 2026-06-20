"""Deny-by-default operator API boundary."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from app.api.auth_dependencies import get_identity_service, require_operator_principal
from app.services.identity.schemas import CurrentUserResponse, Principal
from app.services.identity.service import IdentityService


router = APIRouter(prefix="/admin")


@router.get("/me", response_model=CurrentUserResponse)
async def current_operator(
    principal: Principal = Depends(require_operator_principal),
    identity_service: IdentityService = Depends(get_identity_service),
) -> CurrentUserResponse:
    current = await asyncio.to_thread(identity_service.get_current_user, principal)
    if current is None:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return current
