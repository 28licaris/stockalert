"""Sector rotation (RRG) subsystem.

Classifies market groups (Phase 1: the 11 SPDR sector ETFs) into the four
RRG quadrants — Leading / Weakening / Improving / Lagging — relative to a
benchmark (SPY). See `docs/sector_rotation_spec.md`.

The public surface is the contracts in `schemas` and
`SectorRotationService` (built via `from_settings()`). Everything consumes
the `RotationGroup` abstraction, never a raw symbol — so the Phase 2 theme
catalog (multi-stock baskets) drops in by adding a group kind + resolver,
with no change to the math, the API, or the UI.
"""
from app.services.sectors.schemas import (
    GroupKind,
    Quadrant,
    RotationDashboard,
    RotationGroup,
    RotationPoint,
    SectorRotationState,
)
from app.services.sectors.service import SectorRotationService

__all__ = [
    "GroupKind",
    "Quadrant",
    "RotationDashboard",
    "RotationGroup",
    "RotationPoint",
    "SectorRotationState",
    "SectorRotationService",
]
