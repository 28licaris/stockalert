"""FastAPI dependencies for authenticated customer and operator boundaries."""
from __future__ import annotations

import hmac
from datetime import timedelta
from functools import lru_cache

from fastapi import Depends, HTTPException, Request, status

from app.services.identity.auth_service import OAuthAuthenticationService
from app.services.identity.cognito import CognitoIdentityProvider
from app.services.identity.schemas import Principal
from app.services.identity.repository import PostgresIdentityRepository
from app.services.identity.service import IdentityService


@lru_cache(maxsize=1)
def get_identity_service() -> IdentityService:
    from app.config import settings

    if not settings.auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Customer authentication is disabled.",
            headers={"X-Error-Code": "auth_disabled"},
        )
    if not settings.identity_database_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Customer authentication is not configured.",
            headers={"X-Error-Code": "auth_not_configured"},
        )
    repository = PostgresIdentityRepository.from_settings()
    return IdentityService(repository=repository)


@lru_cache(maxsize=1)
def get_authentication_service() -> OAuthAuthenticationService:
    from app.config import settings

    if not settings.auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Customer authentication is disabled.",
            headers={"X-Error-Code": "auth_disabled"},
        )
    required = {
        "COGNITO_DOMAIN": settings.cognito_domain,
        "COGNITO_ISSUER_URL": settings.cognito_issuer_url,
        "COGNITO_CLIENT_ID": settings.cognito_client_id,
        "IDENTITY_DATABASE_URL": settings.identity_database_url,
    }
    missing = [name for name, value in required.items() if not value]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Customer authentication is not configured.",
            headers={"X-Error-Code": "auth_not_configured"},
        )
    repository = PostgresIdentityRepository.from_settings()
    identity_service = IdentityService(repository=repository)
    return OAuthAuthenticationService(
        provider=CognitoIdentityProvider.from_settings(),
        repository=repository,
        identity_service=identity_service,
        redirect_uri=settings.cognito_redirect_uri,
        logout_uri=settings.cognito_logout_uri,
        session_ttl=timedelta(hours=settings.auth_session_hours),
        transaction_ttl=timedelta(
            minutes=settings.auth_login_transaction_minutes
        ),
    )


def get_optional_principal(
    request: Request,
    identity_service: IdentityService = Depends(get_identity_service),
) -> Principal | None:
    from app.config import settings

    if not settings.auth_enabled:
        return None
    if not settings.identity_database_url:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Customer authentication is not configured.",
            headers={"X-Error-Code": "auth_not_configured"},
        )
    token = request.cookies.get(settings.auth_cookie_name, "")
    if not token:
        return None
    return identity_service.authenticate_session(token)


def get_principal(
    principal: Principal | None = Depends(get_optional_principal),
) -> Principal:
    from app.config import settings

    if not settings.auth_enabled:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Customer authentication is disabled.",
            headers={"X-Error-Code": "auth_disabled"},
        )
    if principal is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={
                "WWW-Authenticate": "Session",
                "X-Error-Code": "unauthorized",
            },
        )
    return principal


def require_operator_principal(
    principal: Principal = Depends(get_principal),
) -> Principal:
    if "operator.access" not in principal.permissions:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Operator access required.",
            headers={"X-Error-Code": "forbidden"},
        )
    return principal


def require_csrf(
    request: Request,
    principal: Principal,
    identity_service: IdentityService,
) -> None:
    from app.config import settings

    header_token = request.headers.get("X-CSRF-Token", "")
    cookie_token = request.cookies.get(settings.auth_csrf_cookie_name, "")
    if (
        not header_token
        or not cookie_token
        or not hmac.compare_digest(header_token, cookie_token)
        or not identity_service.validate_csrf(principal.session_id, header_token)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF validation failed.",
            headers={"X-Error-Code": "csrf_failed"},
        )


def clear_auth_dependency_caches() -> None:
    """Test/process-reset hook; closes are owned by app lifecycle later."""
    get_authentication_service.cache_clear()
    get_identity_service.cache_clear()
