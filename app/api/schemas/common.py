"""
Cross-cutting primitives every cockpit endpoint reuses.

Rules these enforce (see docs/frontend_api_contracts.md §3):
  - Error envelope is uniform across the API. Routes never raise
    `HTTPException(detail="...")` — the global exception handler in
    `app/main_api.py` translates them into `ErrorResponse` envelopes.
  - Pagination shape is uniform. Unbounded lists wrap in `Page[T]`.
  - Asset type is explicit on every symbol-bearing schema (futures vs
    equities have different normalization + market-hours rules).
  - Interval strings are constrained to a known set.
"""
from __future__ import annotations

from datetime import datetime
from typing import Generic, Literal, Optional, TypeVar

from pydantic import BaseModel, ConfigDict, Field


# ─────────────────────────────────────────────────────────────────────
# Symbol / asset shape
# ─────────────────────────────────────────────────────────────────────

AssetType = Literal["EQUITY", "FUTURE", "OPTION", "INDEX", "FUND"]
"""
Closed enum of asset types every schema with a `symbol` field must
also carry. Futures normalize as `/MNQM26`; equities as `AAPL`.
"""


Interval = Literal["1m", "5m", "15m", "30m", "1h", "4h", "1d"]
"""
Bar-interval string. Matches the set in
`app.db.queries.SUPPORTED_INTERVALS` so the boundary stays in sync
with the live ClickHouse readers.
"""


# ─────────────────────────────────────────────────────────────────────
# Health state — used by the Status page + every per-subsystem probe
# ─────────────────────────────────────────────────────────────────────

HealthState = Literal["ok", "warn", "error", "unknown"]
"""
Traffic-light state for any subsystem probe. `unknown` means "not
configured / not checked" (gray pill); `warn` / `error` are operator-
actionable failures.
"""


# ─────────────────────────────────────────────────────────────────────
# Error envelope
# ─────────────────────────────────────────────────────────────────────


class ErrorResponse(BaseModel):
    """
    Uniform error envelope. Every non-2xx response from `/api/v1/*`
    serializes to this shape. Components in the cockpit consume
    `code` for branching (`'not_found'` vs `'rate_limited'`) and
    `message` for direct display.

    `details` carries field-level errors, retry-after seconds,
    quota-info, etc. — the long-tail of typed extras.

    `request_id` is set by middleware when present so support / log
    correlation is one click.
    """

    code: str = Field(
        ...,
        description="Machine-readable error code. Examples: 'validation_error', 'not_found', 'conflict', 'rate_limited', 'unauthorized', 'forbidden', 'unprocessable', 'internal_error'.",
    )
    message: str = Field(
        ...,
        description="Operator-readable message. Safe to surface in cockpit UI.",
    )
    details: Optional[dict] = Field(
        default=None,
        description="Additional structured context (field errors, retry-after, etc.). Shape varies by code.",
    )
    request_id: Optional[str] = Field(
        default=None,
        description="Request correlation ID; matches the X-Request-ID header when middleware is configured.",
    )

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "code": "not_found",
                "message": "Watchlist 'qa-symbols' does not exist.",
                "details": {"watchlist_name": "qa-symbols"},
                "request_id": "01HXY1234567",
            }
        }
    )


# ─────────────────────────────────────────────────────────────────────
# Pagination envelope
# ─────────────────────────────────────────────────────────────────────


T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """
    Standard pagination envelope. Unbounded lists wrap in this shape
    so the cockpit can scroll without re-typing its handler for
    every endpoint.

    `cursor` is opaque — pass back unchanged to fetch the next page.
    `total` is populated when computing it is cheap; null otherwise
    so a count(*) is never forced.
    """

    items: list[T]
    cursor: Optional[str] = Field(
        default=None,
        description="Opaque cursor for the next page. `null` when there is no next page.",
    )
    total: Optional[int] = Field(
        default=None,
        description="Total matching items if cheap to compute; `null` otherwise.",
    )


# ─────────────────────────────────────────────────────────────────────
# Trivial OK envelope for mutations that have nothing else to return
# ─────────────────────────────────────────────────────────────────────


class OkResponse(BaseModel):
    """`{"ok": true}` plus an optional message — for mutations whose return shape is intentionally minimal."""

    ok: Literal[True] = True
    message: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────
# Datetime helper (used inside route handlers, not exported as a model)
# ─────────────────────────────────────────────────────────────────────


def isoformat_z(value: Optional[datetime]) -> Optional[str]:
    """
    Render a datetime as ISO 8601 with a `Z` suffix when naive.
    Mirrors the `_ts()` helpers scattered across legacy route files
    so we can collapse them onto one canonical function over time.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.isoformat()
