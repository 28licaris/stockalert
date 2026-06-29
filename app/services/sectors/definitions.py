"""Registry of rotation groups.

Phase 1: the 11 S&P 500 SPDR sector ETFs, each an `EtfGroup` (single-member
passthrough) scored against SPY. The registry is the seam for Phase 2 — a
theme catalog appends `RotationGroup(kind="basket", members=[…])` entries
here (and `resolver` learns to aggregate them); nothing else changes.
"""
from __future__ import annotations

from app.config import settings
from app.services.sectors.schemas import RotationGroup

# (symbol, display name) for the 11 SPDR sectors. Order is the conventional
# GICS ordering used in most sector dashboards.
_SPDR_SECTORS: list[tuple[str, str]] = [
    ("XLK", "Technology"),
    ("XLC", "Communication Services"),
    ("XLY", "Consumer Discretionary"),
    ("XLP", "Consumer Staples"),
    ("XLE", "Energy"),
    ("XLF", "Financials"),
    ("XLV", "Health Care"),
    ("XLI", "Industrials"),
    ("XLB", "Materials"),
    ("XLRE", "Real Estate"),
    ("XLU", "Utilities"),
]


# Thematic baskets (Phase 2): each resolves to an equal-weighted composite of
# its constituents vs the benchmark. `id` is a short, ticker-style label used
# on the scatter; `name` is the display name; `members` are the holdings shown
# on the page. Add a theme = one entry here (no engine/API/UI change).
_THEMES: list[dict] = [
    {
        "id": "MINERS",
        "name": "Precious Metals Miners",
        "members": [
            # gold miners + royalty/streamers
            "NEM", "GOLD", "AEM", "KGC", "AU", "FNV", "WPM", "RGLD",
            # silver miners
            "PAAS", "CDE", "HL", "AG",
        ],
    },
]


def benchmark_symbol() -> str:
    """The benchmark every group is measured against (default SPY)."""
    return settings.rrg_benchmark


def theme_groups(benchmark: str | None = None) -> list[RotationGroup]:
    """The thematic baskets (Phase 2) — equal-weighted composites."""
    bench = benchmark or benchmark_symbol()
    return [
        RotationGroup(
            id=t["id"],
            name=t["name"],
            kind="basket",
            benchmark=bench,
            members=list(t["members"]),
        )
        for t in _THEMES
    ]


def default_groups(benchmark: str | None = None) -> list[RotationGroup]:
    """The full group set: 11 SPDR sector ETFs + thematic baskets vs SPY."""
    bench = benchmark or benchmark_symbol()
    etfs = [
        RotationGroup(id=sym, name=name, kind="etf", benchmark=bench, members=[sym])
        for sym, name in _SPDR_SECTORS
    ]
    return etfs + theme_groups(bench)


def sector_symbols() -> list[str]:
    """Flat symbol list (sectors + benchmark) — used by the universe-add
    and coverage-verification ops steps."""
    return [sym for sym, _ in _SPDR_SECTORS] + [benchmark_symbol()]


def theme_symbols() -> list[str]:
    """Flat list of all theme constituents — used by the onboarding step."""
    seen: dict[str, None] = {}
    for t in _THEMES:
        for s in t["members"]:
            seen.setdefault(s, None)
    return list(seen)
