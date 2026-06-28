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


def benchmark_symbol() -> str:
    """The benchmark every group is measured against (default SPY)."""
    return settings.rrg_benchmark


def default_groups(benchmark: str | None = None) -> list[RotationGroup]:
    """The Phase-1 group set: 11 SPDR sector ETFs vs the benchmark."""
    bench = benchmark or benchmark_symbol()
    return [
        RotationGroup(
            id=sym,
            name=name,
            kind="etf",
            benchmark=bench,
            members=[sym],
        )
        for sym, name in _SPDR_SECTORS
    ]


def sector_symbols() -> list[str]:
    """Flat symbol list (sectors + benchmark) — used by the universe-add
    and coverage-verification ops steps."""
    return [sym for sym, _ in _SPDR_SECTORS] + [benchmark_symbol()]
