"""Authenticated customer boundary probes and current-user contract."""
from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.auth_dependencies import get_identity_service, get_principal, require_csrf
from app.services.identity.service import IdentityService
from app.services.identity.schemas import (
    CurrentUserResponse,
    Principal,
    SessionListResponse,
    SessionRevocationResponse,
    SecurityEventListResponse,
)


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


@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(
    principal: Principal = Depends(get_principal),
    identity_service: IdentityService = Depends(get_identity_service),
) -> SessionListResponse:
    return await asyncio.to_thread(identity_service.list_sessions, principal)


@router.get("/security-events", response_model=SecurityEventListResponse)
async def list_security_events(
    principal: Principal = Depends(get_principal),
    identity_service: IdentityService = Depends(get_identity_service),
) -> SecurityEventListResponse:
    return await asyncio.to_thread(identity_service.list_security_events, principal)


@router.delete("/sessions/{session_id}", response_model=SessionRevocationResponse)
async def revoke_session(
    session_id: UUID,
    request: Request,
    principal: Principal = Depends(get_principal),
    identity_service: IdentityService = Depends(get_identity_service),
) -> SessionRevocationResponse:
    require_csrf(request, principal, identity_service)
    result = await asyncio.to_thread(
        identity_service.revoke_session_for_principal, principal, session_id
    )
    if result.status == "not_found":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Session not found."
        )
    if result.status == "denied":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Use sign out to revoke the current session.",
        )
    if result.status == "error":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Session could not be revoked.",
        )
    return SessionRevocationResponse(
        revoked_count=1 if result.status == "revoked" else 0
    )


@router.post("/sessions/revoke-others", response_model=SessionRevocationResponse)
async def revoke_other_sessions(
    request: Request,
    principal: Principal = Depends(get_principal),
    identity_service: IdentityService = Depends(get_identity_service),
) -> SessionRevocationResponse:
    require_csrf(request, principal, identity_service)
    result = await asyncio.to_thread(identity_service.revoke_other_sessions, principal)
    if result.status == "error":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Sessions could not be revoked.",
        )
    return SessionRevocationResponse(revoked_count=result.revoked_count)
