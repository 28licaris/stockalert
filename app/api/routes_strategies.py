"""
Strategy library API — the subscription product surface.

Owner endpoints expose full definitions (incl. the secret config); subscriber
endpoints return ONLY redacted cards (StrategyPublic) + actionable alerts
(StrategyAlert) — the config is structurally absent from those response models,
so the recipe cannot leak. Every save is backed up to S3.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.services.sim.library.schemas import (
    BackupResult, StrategyAlert, StrategyDefinition, StrategyPublic,
)
from app.services.sim.library import service as lib

router = APIRouter()


# ── entitlement gate (STUB) ──────────────────────────────────────────
def require_subscription() -> None:
    """Placeholder subscription gate. TODO: wire to identity/Cognito + Stripe
    entitlements (billing is parked). The security guarantee here does NOT rely on
    this stub — subscriber endpoints return redacted models with no config field."""
    return None


# ── owner endpoints (full definitions; admin-gate in prod) ───────────
@router.post("/strategies", response_model=BackupResult)
def register_strategy(definition: StrategyDefinition) -> BackupResult:
    """Register/update a strategy: persist locally + back up the full copy to S3."""
    return lib.register(definition)


@router.get("/strategies", response_model=list[StrategyDefinition])
def list_strategies() -> list[StrategyDefinition]:
    """OWNER view — full definitions including config. Admin-only in production."""
    return lib.list_definitions()


@router.get("/strategies/{name}", response_model=StrategyDefinition)
def get_strategy(name: str) -> StrategyDefinition:
    d = lib.load_definition(name)
    if d is None:
        raise HTTPException(status_code=404, detail=f"strategy {name!r} not found")
    return d


# ── subscriber endpoints (REDACTED; entitlement-gated) ───────────────
@router.get("/library", response_model=list[StrategyPublic], dependencies=[Depends(require_subscription)])
def library() -> list[StrategyPublic]:
    """Subscriber catalog — redacted cards (no config)."""
    return lib.list_public()


@router.get("/library/{name}", response_model=StrategyPublic, dependencies=[Depends(require_subscription)])
def library_card(name: str) -> StrategyPublic:
    d = lib.load_definition(name)
    if d is None or d.visibility not in ("subscribers", "public"):
        raise HTTPException(status_code=404, detail=f"strategy {name!r} not available")
    return lib.to_public(d)


@router.get("/library/{name}/alerts", response_model=list[StrategyAlert], dependencies=[Depends(require_subscription)])
def library_alerts(
    name: str,
    start: Optional[datetime] = Query(None, description="Only alerts on/after this date."),
) -> list[StrategyAlert]:
    """Actionable alerts (entry/stop/target + closed P&L) — no recipe."""
    d = lib.load_definition(name)
    if d is None or d.visibility not in ("subscribers", "public"):
        raise HTTPException(status_code=404, detail=f"strategy {name!r} not available")
    return lib.get_alerts(name, start=start)
