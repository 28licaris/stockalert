"""Public browser authentication routes backed by Cognito managed login."""
from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse

from app.api.auth_dependencies import (
    get_authentication_service,
    get_identity_service,
    get_principal,
    require_csrf,
)
from app.services.identity.auth_service import OAuthAuthenticationService
from app.services.identity.schemas import LogoutResponse, Principal
from app.services.identity.service import IdentityService


router = APIRouter(prefix="/auth")


@router.get("/login", include_in_schema=False)
async def login(
    return_to: str | None = Query(default=None, max_length=500),
    provider: Literal["Google"] | None = Query(default=None),
    mode: Literal["login", "signup"] = Query(default="login"),
    auth: OAuthAuthenticationService = Depends(get_authentication_service),
) -> RedirectResponse:
    result = await auth.begin_login(
        return_to=return_to,
        identity_provider=provider,
        screen_hint="signup" if mode == "signup" else None,
        prompt="login" if mode == "login" else None,
    )
    if result.status != "ok" or result.authorization_url is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Unable to start authentication.",
            headers={"X-Error-Code": result.error_code or "auth_start_failed"},
        )
    return RedirectResponse(result.authorization_url, status_code=302)


@router.get("/password-reset", include_in_schema=False)
async def password_reset(
    auth: OAuthAuthenticationService = Depends(get_authentication_service),
) -> RedirectResponse:
    return RedirectResponse(auth.password_reset_url(), status_code=302)


@router.get("/callback", include_in_schema=False)
async def callback(
    code: str = Query(min_length=1, max_length=4096),
    state_value: str = Query(alias="state", min_length=32, max_length=256),
    auth: OAuthAuthenticationService = Depends(get_authentication_service),
) -> RedirectResponse:
    from app.config import settings

    result = await auth.complete_login(code=code, state=state_value)
    if result.status != "ok" or result.issued_session is None:
        status_code = 400 if result.status in {
            "invalid_state", "expired", "replayed", "identity_conflict"
        } else 503
        raise HTTPException(
            status_code=status_code,
            detail="Authentication callback failed.",
            headers={"X-Error-Code": result.error_code or "auth_callback_failed"},
        )

    issued = result.issued_session
    response = RedirectResponse(result.return_to or "/app/", status_code=303)
    max_age = settings.auth_session_hours * 60 * 60
    response.set_cookie(
        settings.auth_cookie_name,
        issued.token.get_secret_value(),
        max_age=max_age,
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        settings.auth_csrf_cookie_name,
        issued.csrf_token.get_secret_value(),
        max_age=max_age,
        secure=settings.auth_cookie_secure,
        httponly=False,
        samesite="lax",
        path="/",
    )
    return response


@router.post("/logout", response_model=LogoutResponse)
async def logout(
    request: Request,
    principal: Principal = Depends(get_principal),
    auth: OAuthAuthenticationService = Depends(get_authentication_service),
    identity_service: IdentityService = Depends(get_identity_service),
) -> JSONResponse:
    from app.config import settings

    require_csrf(request, principal, identity_service)
    await asyncio.to_thread(auth.revoke_session, principal.session_id)
    response = JSONResponse(
        LogoutResponse(redirect_url=auth.logout_url()).model_dump(mode="json")
    )
    response.delete_cookie(settings.auth_cookie_name, path="/")
    response.delete_cookie(settings.auth_csrf_cookie_name, path="/")
    return response
