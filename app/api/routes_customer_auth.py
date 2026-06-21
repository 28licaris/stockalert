"""Authenticated customer boundary probes and current-user contract."""
from __future__ import annotations

import asyncio
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.api.auth_dependencies import (
    get_identity_service,
    get_mfa_service,
    get_principal,
    require_csrf,
)
from app.services.identity.mfa_service import MfaService, MfaServiceError
from app.services.identity.service import IdentityService
from app.services.identity.schemas import (
    CurrentUserResponse,
    Principal,
    SessionListResponse,
    SessionRevocationResponse,
    SecurityEventListResponse,
    MfaEnrollmentResponse,
    MfaStatusResponse,
    MfaVerificationResponse,
    VerifyMfaEnrollmentRequest,
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


@router.get("/mfa", response_model=MfaStatusResponse)
async def mfa_status(
    principal: Principal = Depends(get_principal),
    mfa_service: MfaService = Depends(get_mfa_service),
) -> MfaStatusResponse:
    try:
        return await asyncio.to_thread(mfa_service.status, principal)
    except MfaServiceError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers={"X-Error-Code": exc.code},
        ) from exc


@router.post("/mfa/enrollment", response_model=MfaEnrollmentResponse)
async def begin_mfa_enrollment(
    request: Request,
    principal: Principal = Depends(get_principal),
    identity_service: IdentityService = Depends(get_identity_service),
    mfa_service: MfaService = Depends(get_mfa_service),
) -> MfaEnrollmentResponse:
    require_csrf(request, principal, identity_service)
    try:
        return await asyncio.to_thread(mfa_service.begin_enrollment, principal)
    except MfaServiceError as exc:
        raise _mfa_http_error(exc) from exc


@router.post("/mfa/enrollment/verify", response_model=MfaVerificationResponse)
async def verify_mfa_enrollment(
    payload: VerifyMfaEnrollmentRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    identity_service: IdentityService = Depends(get_identity_service),
    mfa_service: MfaService = Depends(get_mfa_service),
) -> MfaVerificationResponse:
    require_csrf(request, principal, identity_service)
    try:
        return await asyncio.to_thread(
            mfa_service.verify_enrollment, principal, payload.code
        )
    except MfaServiceError as exc:
        raise _mfa_http_error(exc) from exc


def _mfa_http_error(exc: MfaServiceError) -> HTTPException:
    if exc.code == "reauthentication_required":
        status_code = status.HTTP_409_CONFLICT
    elif exc.code in {"invalid_mfa_code", "mfa_not_supported"}:
        status_code = status.HTTP_400_BAD_REQUEST
    else:
        status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HTTPException(
        status_code=status_code,
        detail=str(exc),
        headers={"X-Error-Code": exc.code},
    )


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
