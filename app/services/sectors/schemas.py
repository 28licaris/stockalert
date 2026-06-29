"""Pydantic contracts for the sector-rotation (RRG) subsystem.

These shapes are the boundary the HTTP route and the frontend bind to.
They are deliberately group-kind agnostic: an ETF and a Phase-2 stock
basket both produce the same `SectorRotationState`, so the API/UI never
learn how a group's price series was sourced.
"""
from __future__ import annotations

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field

# RRG quadrants. Centered on the benchmark (RS-Ratio / RS-Momentum = 100).
Quadrant = Literal["leading", "weakening", "improving", "lagging"]

# How a group resolves to a price series. "etf" = single ETF passthrough
# (Phase 1). "basket" = aggregated index of N constituents (Phase 2).
GroupKind = Literal["etf", "basket"]


class RotationGroup(BaseModel):
    """A market group scored against the benchmark.

    Phase 1 groups are all `kind="etf"` with a single member. The
    `members`/`weights` fields exist now so a Phase-2 `basket` needs no
    schema change — only a resolver implementation.
    """

    id: str = Field(..., description="Stable key, e.g. 'XLK' or 'gold-miners'.")
    name: str = Field(..., description="Display name, e.g. 'Technology'.")
    label: str = Field("", description="Short chart label for the scatter dot; falls back to id.")
    kind: GroupKind = "etf"
    benchmark: str = Field(..., description="Benchmark symbol, e.g. 'SPY'.")
    members: list[str] = Field(
        ..., description="Symbols composing the group. ['XLK'] for an ETF."
    )
    weights: Optional[dict[str, float]] = Field(
        None,
        description="Per-member weights for a basket. None ⇒ equal weight. "
        "Unused for kind='etf'.",
    )


class RotationPoint(BaseModel):
    """One RRG sample: the two axes + the quadrant they fall in."""

    date: date
    rs_ratio: float = Field(..., description="Relative strength vs benchmark, ~100.")
    rs_momentum: float = Field(..., description="Momentum of RS-Ratio, ~100.")
    quadrant: Quadrant


class SectorRotationState(BaseModel):
    """A single group's current RRG position plus its recent trajectory."""

    group_id: str
    name: str
    label: str = ""
    kind: GroupKind = "etf"
    members: list[str] = Field(
        default_factory=list,
        description="Constituent tickers (the ETF itself for kind='etf'; the "
        "basket holdings for kind='basket') — lets the UI show what's inside.",
    )
    current: RotationPoint
    tail: list[RotationPoint] = Field(
        default_factory=list,
        description="Weekly RRG points, oldest → newest, for the scatter tail.",
    )
    relative_strength: list[tuple[date, float]] = Field(
        default_factory=list,
        description="The raw relative-strength line (group/benchmark, indexed "
        "to 100 at the window start) for the trend chart.",
    )


class ExcludedGroup(BaseModel):
    """A group that could not be scored — surfaced, never silently dropped."""

    group_id: str
    reason: str


class RotationDashboard(BaseModel):
    """The full payload for the rotation page."""

    benchmark: str
    as_of: date
    tail_weeks: int
    sectors: list[SectorRotationState] = Field(default_factory=list)
    excluded: list[ExcludedGroup] = Field(
        default_factory=list,
        description="Groups dropped for insufficient/absent data, with reasons.",
    )


# ── Themes as data (editable at runtime via API / MCP / UI) ──────────


class ThemeRecord(BaseModel):
    """A persisted thematic basket (a row in the `sector_themes` store)."""

    theme_id: str
    name: str
    label: str
    members: list[str]
    weights: dict[str, float] = Field(default_factory=dict, description="Empty ⇒ equal weight.")
    benchmark: str = "SPY"
    is_active: bool = True


class ThemeCreateRequest(BaseModel):
    """Create a theme. `members` are tickers; equal-weight unless `weights`
    given. `label` (short chart code) and `theme_id` default from `name`."""

    name: str = Field(..., min_length=1, description="Display name, e.g. 'Copper Miners'.")
    members: list[str] = Field(..., min_length=1, description="Constituent tickers.")
    label: str | None = Field(None, description="Short chart label; defaults from name.")
    weights: dict[str, float] | None = None
    benchmark: str = "SPY"


class ThemeMutationResponse(BaseModel):
    theme: ThemeRecord | None = None
    onboarded: list[str] = Field(
        default_factory=list,
        description="Constituents newly added to the streaming universe.",
    )
    themes: list[ThemeRecord] = Field(default_factory=list)
