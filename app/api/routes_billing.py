"""Customer subscription billing routes (Stripe Checkout / Portal / webhook)."""
from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status

from app.api.auth_dependencies import (
    get_billing_service,
    get_identity_service,
    get_principal,
    require_csrf,
)
from app.services.billing.service import BillingService, BillingServiceError
from app.services.billing.schemas import (
    CheckoutRequest,
    CheckoutSessionResponse,
    PortalSessionResponse,
    SubscriptionStatusResponse,
)
from app.services.identity.schemas import Principal
from app.services.identity.service import IdentityService


router = APIRouter(prefix="/customer/billing")


def _http_error(exc: BillingServiceError) -> HTTPException:
    if exc.code in {"plan_not_configured", "billing_not_configured"}:
        code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif exc.code == "no_billing_account":
        code = status.HTTP_409_CONFLICT
    else:
        code = status.HTTP_502_BAD_GATEWAY
    return HTTPException(
        status_code=code, detail=str(exc), headers={"X-Error-Code": exc.code}
    )


@router.get("", response_model=SubscriptionStatusResponse)
async def billing_status(
    principal: Principal = Depends(get_principal),
    billing: BillingService = Depends(get_billing_service),
) -> SubscriptionStatusResponse:
    try:
        return await asyncio.to_thread(billing.get_status, principal.tenant_id)
    except BillingServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/checkout", response_model=CheckoutSessionResponse)
async def create_checkout(
    payload: CheckoutRequest,
    request: Request,
    principal: Principal = Depends(get_principal),
    identity_service: IdentityService = Depends(get_identity_service),
    billing: BillingService = Depends(get_billing_service),
) -> CheckoutSessionResponse:
    require_csrf(request, principal, identity_service)
    try:
        return await asyncio.to_thread(
            billing.create_checkout_session, principal.tenant_id, payload.plan
        )
    except BillingServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/portal", response_model=PortalSessionResponse)
async def create_portal(
    request: Request,
    principal: Principal = Depends(get_principal),
    identity_service: IdentityService = Depends(get_identity_service),
    billing: BillingService = Depends(get_billing_service),
) -> PortalSessionResponse:
    require_csrf(request, principal, identity_service)
    try:
        return await asyncio.to_thread(
            billing.create_portal_session, principal.tenant_id
        )
    except BillingServiceError as exc:
        raise _http_error(exc) from exc


@router.post("/webhook")
async def stripe_webhook(
    request: Request,
    billing: BillingService = Depends(get_billing_service),
) -> Response:
    # Stripe calls this unauthenticated; trust is established by the signature,
    # verified inside the service against the raw request body.
    payload = await request.body()
    sig_header = request.headers.get("Stripe-Signature", "")
    try:
        await asyncio.to_thread(billing.handle_webhook, payload, sig_header)
    except BillingServiceError as exc:
        if exc.code in {"webhook_signature_invalid", "webhook_not_configured"}:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
                headers={"X-Error-Code": exc.code},
            ) from exc
        # Persistence/internal failures: 500 so Stripe retries the delivery.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
            headers={"X-Error-Code": exc.code},
        ) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
