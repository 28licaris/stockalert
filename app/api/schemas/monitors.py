"""
Monitor response schemas. Backs `/api/v1/monitors[/start|/stop]`.

Each "monitor" is a long-running task that watches one or more symbols
for divergence signals against a configured indicator. Lifecycle
(start / stop / list) lives here; the actual signal stream lands in
the live `signals` topic (FE-CONTRACTS-7 WebSocket multiplex).

Wire shape preserved from `monitor_manager.list_monitors()`:
`{ "<key>": MonitorInfo, ... }` — an OBJECT keyed by monitor identity,
not a list. Cockpit consumers iterate `Object.entries(monitors)`.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class MonitorInfo(BaseModel):
    """A single running monitor's state."""

    tickers: list[str] = Field(
        ..., description="Symbols this monitor task is watching."
    )
    indicator: str = Field(
        ...,
        description="Indicator name driving the rule (e.g. 'rsi', 'macd', 'tsi').",
    )
    signal_type: str = Field(
        ...,
        description="Type of divergence the monitor fires on (e.g. 'hidden_bullish_divergence').",
    )
    status: str = Field(
        ...,
        description="One of: 'running', 'completed', 'cancelled', 'failed: <msg>', 'unknown'.",
    )
    started_at: Optional[str] = Field(
        default=None, description="ISO 8601 with `Z` suffix."
    )


# `GET /api/v1/monitors` returns a bare dict keyed by monitor identity
# (e.g. {"rsi:AAPL:hidden_bullish_divergence": MonitorInfo, ...}).
# The route declares `response_model=dict[str, MonitorInfo]` directly
# rather than wrapping in a list; that preserves the legacy wire
# shape and the cockpit reads it as `Record<string, MonitorInfo>`.


class MonitorRequest(BaseModel):
    """Body for `POST /api/v1/monitors/start` and `/stop`."""

    tickers: list[str]
    indicator: str = "rsi"
    signal_type: str = "hidden_bullish_divergence"


class MonitorActionResponse(BaseModel):
    """Response from start/stop mutations."""

    status: str = Field(
        ...,
        description="'success' on the happy path; failure modes are surfaced as HTTPException with ErrorResponse envelope.",
    )
    message: str
    details: dict = Field(
        default_factory=dict,
        description="Per-ticker result from the monitor manager (free-form; mirrors the existing dashboard shape).",
    )
