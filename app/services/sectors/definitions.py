"""Registry of rotation groups.

The 11 S&P 500 SPDR sector ETFs are **built-in** (`_SPDR_SECTORS`, fixed/
standard). Thematic baskets are **data** — loaded from the `sector_themes`
ClickHouse store (`theme_store`), so they're created/edited at runtime via the
API / MCP / UI without a code change. Both kinds become `RotationGroup`s the
engine/API/UI consume identically.
"""
from __future__ import annotations

import logging

from app.config import settings
from app.services.sectors.schemas import RotationGroup

logger = logging.getLogger(__name__)

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


def sector_etf_groups(benchmark: str | None = None) -> list[RotationGroup]:
    """The 11 built-in SPDR sector ETF groups."""
    bench = benchmark or benchmark_symbol()
    return [
        RotationGroup(id=sym, name=name, label=sym, kind="etf",
                      benchmark=bench, members=[sym])
        for sym, name in _SPDR_SECTORS
    ]


def theme_groups(benchmark: str | None = None) -> list[RotationGroup]:
    """Thematic baskets from the `sector_themes` store. Returns [] (logged) if
    the store is unreachable — sectors still render."""
    bench = benchmark or benchmark_symbol()
    try:
        from app.services.sectors import theme_store
        records = theme_store.list_themes()
    except Exception as exc:  # noqa: BLE001 — degrade to sectors-only
        logger.error("theme_groups: could not load themes: %s", exc)
        return []
    return [
        RotationGroup(
            id=r.theme_id,
            name=r.name,
            label=r.label or r.theme_id,
            kind="basket",
            benchmark=r.benchmark or bench,
            members=list(r.members),
            weights=r.weights or None,
        )
        for r in records
    ]


def default_groups(benchmark: str | None = None) -> list[RotationGroup]:
    """The full group set: 11 SPDR sector ETFs + stored thematic baskets."""
    bench = benchmark or benchmark_symbol()
    return sector_etf_groups(bench) + theme_groups(bench)


def sector_symbols() -> list[str]:
    """Flat symbol list (sectors + benchmark) — built-in, no store I/O."""
    return [sym for sym, _ in _SPDR_SECTORS] + [benchmark_symbol()]


def theme_symbols() -> list[str]:
    """Flat list of all stored theme constituents — for onboarding/sync."""
    seen: dict[str, None] = {}
    for g in theme_groups():
        for s in g.members:
            seen.setdefault(s, None)
    return list(seen)
