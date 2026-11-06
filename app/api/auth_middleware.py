"""Deny-by-default session gate for the API, cockpit, websocket and MCP surfaces.

Spec: ``docs/operations/deployment_dev.md`` §5 "The gap that blocks sharing
the URL" (code change C1).

Why a middleware and not a dependency on every router: only six route files
carry an auth dependency, so with ``AUTH_ENABLED=true`` every market-data,
research, options, backtest and strategy endpoint still answered without a
session. A per-router ``Depends`` would have to be remembered on every new
``include_router`` forever; one ASGI gate in front of the router fails closed
by construction. The SPA's ``AuthGuard`` is UX, not security.

Behaviour (only while ``AUTH_ENABLED=true``; otherwise a pure pass-through so
local dev and the unit suite are unchanged):

* ``/api/``, ``/cockpit/``, ``/ws/`` and ``/mcp`` require a valid session
  cookie. Without one: HTTP → ``401`` with ``{"error":
  "authentication_required"}`` (plus the cockpit ``ErrorResponse`` envelope
  fields so the response shape stays uniform); websocket → close ``1008``
  before accept.
* Allowlist, exact or prefix as listed in :data:`ALLOWED_EXACT` /
  :data:`ALLOWED_PREFIXES`: ``/health`` (public liveness — ``/api/health/*``
  stays gated), ``/auth/*`` (login/callback/logout), ``/app`` + ``/app/*``
  (the SPA shell and assets), and the Stripe webhook, whose signature is its
  authentication.
* **Default-deny for non-operators.** Past the login check, a session without
  ``operator.access`` may only reach the explicit customer rules in
  :data:`CUSTOMER_RULES` (method + path prefix, derived from what the SPA calls).
  Everything else answers ``403`` (websocket: close ``1008``) before the route
  runs. A route added tomorrow is therefore operator-only until someone adds a
  customer rule; ``app/api/tests/test_route_policy.py`` fails the build on any
  route that is not classified in :data:`CUSTOMER_RULES` or
  :data:`OPERATOR_RULES` (or reviewed as public), so the omission is loud.
* Operator routers keep ``require_operator`` on top (defence in depth).

The session is resolved by the same cookie → session lookup the route
dependency ``get_optional_principal`` uses (:func:`resolve_session_principal`);
nothing about cookies or tokens is duplicated here. The lookup is a blocking
DB call, so it runs in Starlette's threadpool exactly as the ``Depends`` path
does. Both collaborators (the enabled flag and the resolver) are injected so
unit tests never build an identity service.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import HTTPException
from starlette.concurrency import run_in_threadpool
from starlette.requests import HTTPConnection
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from app.api import auth_dependencies
from app.api.schemas import ErrorResponse
from app.services.identity.permissions import OPERATOR_ACCESS
from app.services.identity.schemas import Principal

logger = logging.getLogger(__name__)

# ── Path policy (spec §5) ─────────────────────────────────────────────
# Allowlist wins over the gated set; everything else is "open" — i.e. not
# this gate's business (``/``, ``/docs``, legacy redirects). Those are either
# harmless or handled at the proxy (spec §10 test 2: ``/docs`` → 403 is Caddy).
GATED_EXACT: frozenset[str] = frozenset({"/mcp"})
GATED_PREFIXES: tuple[str, ...] = ("/api/", "/cockpit/", "/ws/", "/mcp/")
ALLOWED_EXACT: frozenset[str] = frozenset(
    {
        "/health",
        "/app",
        # Spec §5 writes this as ``/api/v1/billing/webhook``; the router is
        # mounted at ``/customer/billing`` under ``/api/v1`` (routes_billing),
        # so this is the path Stripe actually calls.
        "/api/v1/customer/billing/webhook",
    }
)
ALLOWED_PREFIXES: tuple[str, ...] = ("/auth/", "/app/")

GateDecision = Literal["allowlisted", "gated", "open"]

AUTH_REQUIRED_CODE = "authentication_required"
FORBIDDEN_CODE = "forbidden"  # same code ``require_operator`` answers with
WEBSOCKET_POLICY_VIOLATION = 1008


def classify_path(path: str) -> GateDecision:
    """Pure path policy — exact matches first, then prefixes.

    ``/health`` is exact on purpose: ``/health/services`` and
    ``/health/freshness`` live under ``/api/`` and stay gated.
    """
    if path in ALLOWED_EXACT or path.startswith(ALLOWED_PREFIXES):
        return "allowlisted"
    if path in GATED_EXACT or path.startswith(GATED_PREFIXES):
        return "gated"
    return "open"


# ── Role policy: customer vs operator ─────────────────────────────────
@dataclass(frozen=True)
class PolicyRule:
    """One (method, path-prefix) entry of the role policy.

    ``prefix`` matches the path itself or anything below it on a ``/`` boundary
    (``/api/v1/wave`` covers ``/api/v1/wave/AAPL`` but not ``/api/v1/waves``);
    ``exact`` matches the path only. ``methods=None`` means any method
    (including ``WEBSOCKET``). ``HEAD`` is treated as ``GET``.
    """

    prefix: str
    methods: frozenset[str] | None = None
    exact: bool = False

    def matches(self, method: str, path: str) -> bool:
        method = "GET" if method == "HEAD" else method
        if self.methods is not None and method not in self.methods:
            return False
        if path == self.prefix:
            return True
        return not self.exact and path.startswith(self.prefix + "/")


_GET = frozenset({"GET"})
_POST = frozenset({"POST"})
_WRITES = frozenset({"POST", "DELETE", "PUT", "PATCH"})

# What a signed-in non-operator may call. Derived from the SPA's API calls
# (frontend/src) so no customer page breaks; see docs/product/access_control.md.
# Anything NOT matched here is operator-only at runtime.
CUSTOMER_RULES: tuple[PolicyRule, ...] = (
    # Charts / market data (read)
    PolicyRule("/api/v1/bars", _GET),  # incl. /bars/latest
    PolicyRule("/api/v1/signals", _GET),
    PolicyRule("/api/v1/instruments/search", _GET),
    PolicyRule("/api/v1/instruments/lookup", _GET),
    PolicyRule("/api/v1/market/banner", _GET),
    PolicyRule("/api/v1/calendar", _GET),
    PolicyRule("/api/v1/news", _GET),  # incl. /news/digest
    PolicyRule("/api/v1/economic", _GET),  # incl. /{series}/history
    PolicyRule("/api/v1/indicators/chart-data", _POST),
    # Options (hot tier read)
    PolicyRule("/api/v1/options/contracts/latest", _GET),
    PolicyRule("/api/v1/options/gex", _GET),  # incl. /gex/latest
    # Elliott Wave, sectors (themes are read-only for customers), research
    PolicyRule("/api/v1/wave", _GET),
    PolicyRule("/api/v1/sectors/rotation", _GET),
    PolicyRule("/api/v1/sectors/themes", _GET),
    PolicyRule("/api/v1/research/rankings", _GET),
    # Strategy surfaces: forward paper record, redacted library cards
    PolicyRule("/api/v1/paper/status", _GET),
    PolicyRule("/api/v1/paper/export", _GET),
    PolicyRule("/api/v1/library", _GET),
    # Backtest playground. NOTE: the run registry is shared, not per-user.
    PolicyRule("/api/v1/backtest/catalog", _GET),
    PolicyRule("/api/v1/backtest/run", _POST),
    PolicyRule("/api/v1/backtest/runs", _GET),  # incl. /runs/{id}
    # Per-user data (Principal-scoped in the route)
    PolicyRule("/api/v1/customer"),
    PolicyRule("/api/v1/my/watchlists"),
    PolicyRule("/api/v1/my/sector-themes"),
    # Assistant (conversations are scoped to the caller's tenant; tool set is
    # restricted for non-operators in app/services/assistant/policy.py)
    PolicyRule("/cockpit/assistant"),
)

# Explicit operator-only classification. NOT consulted at runtime (runtime is
# default-deny); it exists so the route-policy test can demand that every route
# in the real app is classified one way or the other.
OPERATOR_RULES: tuple[PolicyRule, ...] = (
    # Shared ingest / universe / infra control plane
    PolicyRule("/api/v1/stream"),
    PolicyRule("/api/v1/backfill"),
    PolicyRule("/api/v1/jobs"),
    PolicyRule("/api/v1/clickhouse"),
    PolicyRule("/api/v1/health"),
    PolicyRule("/api/v1/admin"),
    PolicyRule("/api/v1/lake"),
    PolicyRule("/api/v1/monitors"),
    # The operator's own Schwab account data
    PolicyRule("/api/v1/journal"),
    # Shared (global) watchlists, strategies and themes
    PolicyRule("/api/v1/watchlist"),
    PolicyRule("/api/v1/watchlists"),
    PolicyRule("/api/v1/strategies"),
    PolicyRule("/api/v1/sectors/themes", _WRITES),
    # Research / debug / unused-by-the-SPA reads
    PolicyRule("/api/v1/ppl"),
    PolicyRule("/api/v1/screener"),
    PolicyRule("/api/v1/adjusted"),
    PolicyRule("/api/v1/corp-actions"),
    PolicyRule("/api/v1/movers"),
    PolicyRule("/api/v1/indicators/series"),
    PolicyRule("/api/v1/options/contracts", exact=True),
    # Non-HTTP / tooling surfaces and the legacy redirect catch-all
    PolicyRule("/api/{rest:path}", exact=True),
    PolicyRule("/ws"),
    PolicyRule("/mcp"),
)

Tier = Literal["customer", "operator"]


def policy_tier(method: str, path: str) -> Tier:
    """Role required for a gated ``method`` + ``path``. Default-deny: a request
    is customer-accessible only if a :data:`CUSTOMER_RULES` entry matches."""
    if any(rule.matches(method, path) for rule in CUSTOMER_RULES):
        return "customer"
    return "operator"


PrincipalResolver = Callable[[HTTPConnection], Principal | None]


def resolve_session_principal(connection: HTTPConnection) -> Principal | None:
    """Production resolver: the route dependency's own cookie → session lookup.

    The identity service comes from ``get_identity_service`` exactly as a
    route's ``Depends(get_identity_service)`` would get it — including
    ``app.dependency_overrides``, so the test suite injects a fake through the
    same seam for the gate and for the routes. The attributes are looked up on
    the module at call time (not bound at import) so a monkeypatched factory
    also covers ``require_operator``, which calls it directly.
    ``get_optional_principal`` only reads ``.cookies``, which ``HTTPConnection``
    provides for both HTTP and websocket scopes.
    """
    factory = auth_dependencies.get_identity_service
    overrides = getattr(connection.scope.get("app"), "dependency_overrides", {})
    identity_service = overrides.get(factory, factory)()
    return auth_dependencies.get_optional_principal(connection, identity_service)  # type: ignore[arg-type]


def _error_body(code: str, message: str) -> dict[str, Any]:
    body = ErrorResponse(code=code, message=message).model_dump(exclude_none=False)
    # Spec §5 contract: ``{"error": "authentication_required"}``. Kept beside
    # the envelope keys rather than instead of them so ``/api/v1/*`` errors
    # keep one shape (app/api/schemas/common.py).
    body["error"] = code
    return body


class SessionGateMiddleware:
    """Pure ASGI middleware (not ``BaseHTTPMiddleware``) so websocket scopes
    are gated too — ``BaseHTTPMiddleware`` only sees HTTP."""

    def __init__(
        self,
        app: ASGIApp,
        *,
        is_enabled: Callable[[], bool],
        resolve_principal: PrincipalResolver,
    ) -> None:
        self.app = app
        self._is_enabled = is_enabled
        self._resolve_principal = resolve_principal
        logger.info(
            "session gate installed: gated=%s allowlisted=%s (enforced only while AUTH_ENABLED=true)",
            sorted(GATED_EXACT) + [f"{p}*" for p in GATED_PREFIXES],
            sorted(ALLOWED_EXACT) + [f"{p}*" for p in ALLOWED_PREFIXES],
        )

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket") or not self._is_enabled():
            await self.app(scope, receive, send)
            return

        path: str = scope["path"]
        if classify_path(path) != "gated":
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "WEBSOCKET")
        connection = HTTPConnection(scope)
        try:
            principal = await run_in_threadpool(self._resolve_principal, connection)
        except HTTPException as exc:
            # ``get_identity_service`` 503s when auth is enabled but the
            # identity DB is not configured. The middleware sits outside
            # FastAPI's exception handlers, so render the same envelope here
            # rather than let it surface as an opaque 500. Fails closed.
            code = (exc.headers or {}).get("X-Error-Code") or "service_unavailable"
            logger.error(
                "session gate: auth unavailable path=%s method=%s status=%s code=%s",
                path, method, exc.status_code, code,
            )
            await self._deny(scope, receive, send, exc.status_code, code, str(exc.detail), exc.headers)
            return

        if principal is None:
            reason = "no_cookie" if not connection.cookies else "invalid_session"
            logger.info("session gate denied path=%s method=%s reason=%s", path, method, reason)
            await self._deny(
                scope, receive, send, 401, AUTH_REQUIRED_CODE, "Authentication required.",
                {"WWW-Authenticate": "Session", "X-Error-Code": AUTH_REQUIRED_CODE},
            )
            return

        if policy_tier(method, path) == "operator" and OPERATOR_ACCESS not in principal.permissions:
            logger.warning(
                "session gate denied non-operator path=%s method=%s user=%s",
                path, method, principal.user_id,
            )
            await self._deny(
                scope, receive, send, 403, FORBIDDEN_CODE, "Operator access required.",
                {"X-Error-Code": FORBIDDEN_CODE},
            )
            return

        await self.app(scope, receive, send)

    async def _deny(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        code: str,
        message: str,
        headers: dict[str, str] | None,
    ) -> None:
        if scope["type"] == "websocket":
            # Close before accept: uvicorn turns this into an HTTP 403 on the
            # handshake; Starlette's TestClient raises WebSocketDisconnect.
            await send({"type": "websocket.close", "code": WEBSOCKET_POLICY_VIOLATION, "reason": code})
            return
        response = JSONResponse(
            status_code=status_code, content=_error_body(code, message), headers=headers
        )
        await response(scope, receive, send)
