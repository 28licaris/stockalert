"""Public Pydantic contracts for the Elliott Wave engine.

These shapes are what the engine emits and what every consumer (WaveReader, the
HTTP route, the MCP tool, the store) depends on. The engine package imports
these and `app.indicators.pivots`; it imports nothing from `app.db`,
`app.providers`, or `app.services` (purity gate: `tests/test_elliott_purity.py`).
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.indicators.pivots import Pivot  # re-exported for consumers

__all__ = ["Pivot", "WaveCandidate", "WaveLabeling"]


class WaveCandidate(BaseModel):
    """One self-consistent labeling of the recent swings."""

    structure: Literal["impulse", "zigzag"]
    direction: Literal["up", "down"]
    current_wave: str                                   # "1".."5" | "A"|"B"|"C" | "complete"
    degree: int = 0
    pivots: list[Pivot]
    labels: list[str]                                   # label per pivot, e.g. ["0","1","2"]
    rules_passed: dict[str, bool]
    rule_score: float                                   # fraction of applicable hard rules satisfied
    fib_score: float                                    # 0..1 Fibonacci-fit
    confidence: float                                   # raw composite (NOT calibrated) — see spec D6
    probability: float = 0.0                            # confidence normalized across surfaced counts
    invalidation_price: float
    fib_targets: dict[str, float] = Field(default_factory=dict)
    rationale: str = ""
    # v3 nesting (V3-1): each wave validated against its subdivision one degree
    # finer (motive→5, corrective→3). 1.0 = no finer degree to check / fully valid.
    nesting_score: float = 1.0
    subwaves: list[dict] = Field(default_factory=list)


class WaveLabeling(BaseModel):
    """The full picture for one symbol/interval at one as-of bar.

    `primary`/`secondary` are the two surfaced paths; `alternates` holds any
    further valid counts the engine produced. `uncertainty` is the probability
    mass left over after primary+secondary — the honest "no clear count" signal.
    """

    symbol: str
    interval: str
    as_of: datetime
    as_of_index: int
    as_of_price: float
    n_confirmed_swings: int
    primary: Optional[WaveCandidate] = None
    secondary: Optional[WaveCandidate] = None
    alternates: list[WaveCandidate] = Field(default_factory=list)
    current_wave: Optional[str] = None
    confidence: float = 0.0
    uncertainty: float = 1.0
    engine_ver: str
