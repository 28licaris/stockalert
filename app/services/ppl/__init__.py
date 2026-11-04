"""Posterior Predictive Levels — simulated forward-price distributions.

Public surface is `schemas` (DTOs) and `contract` (the `PplBuilder` Protocol).
`service.PplService` is the implementation and must not be imported across
service boundaries (`docs/standards/service_modules.md` rule 1).

Spec: `docs/trading/ppl.md`.
"""
from app.services.ppl.contract import PplBuilder
from app.services.ppl.schemas import (
    MODEL_VERSION,
    SUPPORTED_INTERVALS,
    ConditioningParams,
    PplCalibration,
    PplLevel,
    PplParams,
    PplResult,
    PplSnapshot,
    PplState,
    PplVolModel,
)

__all__ = [
    "MODEL_VERSION",
    "SUPPORTED_INTERVALS",
    "ConditioningParams",
    "PplBuilder",
    "PplCalibration",
    "PplLevel",
    "PplParams",
    "PplResult",
    "PplSnapshot",
    "PplState",
    "PplVolModel",
]
