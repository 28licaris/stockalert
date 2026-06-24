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

__all__ = ["Pivot", "WaveCandidate", "WaveLabeling", "WaveScenario"]


class WaveCandidate(BaseModel):
    """One self-consistent labeling of the recent swings."""

    structure: Literal["impulse", "zigzag", "flat", "triangle", "diagonal"]
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
    # v3 forward plan (V3-2): the projection of the wave being moved INTO
    # (in wave 4 → wave 5), as a confluence zone + invalidation.
    forward: dict = Field(default_factory=dict)
    # v3 structure catalog (V3-5): per-candidate flags for special structures.
    is_truncated: bool = False      # wave 5 failed to exceed wave 3 (impulse only)
    is_diagonal: bool = False       # wave 4 overlaps wave 1 (contracting diagonal)
    diagonal_type: str = ""         # "leading" (wave 1/A position) or "ending" (wave 5/C position)
    # v3 labeled alternates (V3-3): scenario-level fields.
    # confirms_at: the price that "flips" to this count (= previous count's
    #   invalidation_price). None for the primary — it's already in effect.
    # scenario_label: "Primary" | "Secondary" | "Alternate 1" | …
    confirms_at: Optional[float] = None
    scenario_label: str = ""


class WaveScenario(BaseModel):
    """Trader-facing scenario summary for one count.

    V3-3 (R5): each surfaced count — primary + secondary + alternates — is
    packaged as a tradeable scenario with explicit gate prices. A count "flips"
    to the next when the current count's `invalidation` is breached.
    """

    rank: int                   # 1 = primary, 2 = secondary, 3+ = alternates
    label: str                  # "Primary", "Secondary", "Alternate 1", …
    structure: str
    direction: str
    current_wave: str
    probability: float
    invalidation: float         # hard gate — the stop price
    confirms_at: Optional[float]  # None for primary; prev count's invalidation for others
    next_target: Optional[float]  # first fib_target, if any
    what_confirms: str          # human text — what causes this count to become active
    what_invalidates: str       # human text — what kills this count
    rationale: str


class WaveLabeling(BaseModel):
    """The full picture for one symbol/interval at one as-of bar.

    `primary`/`secondary` are the two surfaced paths; `alternates` holds any
    further valid counts the engine produced. `uncertainty` is the probability
    mass left over after primary+secondary — the honest "no clear count" signal.

    V3-3: `scenarios` is the ordered trader-facing list — primary first, then
    secondary, then alternates, each with confirms_at/what_confirms/what_invalidates.
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
    # V3-3: ordered scenario list (primary → secondary → alternates)
    scenarios: list[WaveScenario] = Field(default_factory=list)
