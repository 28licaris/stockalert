"""Pure planning and verification for rebuilding ClickHouse bars from the lake.

``scripts/ch_bootstrap.py`` runs the steps; this module decides what to rebuild
and checks the result, so the logic is testable without any infrastructure.

The source of truth for "what should the rebuilt ClickHouse hold" is the
per-symbol coverage the state snapshot recorded from the ClickHouse it was taken
from. The lake legitimately holds MORE than a given ClickHouse (extended hours,
full history), so verification asks "at least what that ClickHouse had", not
"identical".
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from typing import Iterable

from pydantic import BaseModel

from app.services.lake_integrity.schemas import CoverageRow


class Problem(BaseModel):
    symbol: str
    kind: str
    detail: str


def rebuild_windows(coverage: Iterable[CoverageRow], lake_history_start: date) -> dict[date, list[str]]:
    """Group symbols by the ``--since`` date each needs.

    The equities loader's default window starts at ``lake_history_start``. A
    symbol whose recorded history begins earlier (the 2006-era names) needs its
    own earlier start, or that history would be silently dropped.
    """
    groups: dict[date, list[str]] = defaultdict(list)
    for c in coverage:
        groups[min(c.first.date(), lake_history_start)].append(c.symbol)
    return {d: sorted(syms) for d, syms in sorted(groups.items())}


def verify_coverage(
    expected: Iterable[CoverageRow],
    actual: Iterable[CoverageRow],
    *,
    rows_tolerance: float = 0.95,
    first_slack_days: int = 7,
) -> list[Problem]:
    """Compare a rebuilt table against the recorded coverage. Empty list = verified."""
    have = {a.symbol: a for a in actual}
    problems: list[Problem] = []
    for e in expected:
        a = have.get(e.symbol)
        if a is None:
            problems.append(Problem(symbol=e.symbol, kind="missing_symbol", detail=f"expected {e.rows} rows, found none"))
            continue
        if a.first > e.first + timedelta(days=first_slack_days):
            problems.append(Problem(symbol=e.symbol, kind="history_truncated",
                                    detail=f"starts {a.first.date()}, expected {e.first.date()}"))
        if a.last.date() < e.last.date():
            problems.append(Problem(symbol=e.symbol, kind="stale", detail=f"ends {a.last.date()}, expected {e.last.date()}"))
        if a.rows < e.rows * rows_tolerance:
            problems.append(Problem(symbol=e.symbol, kind="too_few_rows",
                                    detail=f"{a.rows} rows, expected at least {int(e.rows * rows_tolerance)} of {e.rows}"))
    return problems


def split_window(since: date, until: date, max_years: int = 3) -> list[tuple[date, date]]:
    """Split [since, until] into consecutive inclusive segments of at most ``max_years``.

    Rebuilding a symbol's whole history in one call holds all of it in memory at once
    (measured 2026-11-05: ~3.5 GB for one 20-year symbol), so three parallel workers
    exceeded the box's cap and thrashed. Bounded segments keep the memory per worker small.
    """
    if since > until:
        raise ValueError(f"since {since} is after until {until}")
    out: list[tuple[date, date]] = []
    start = since
    while start <= until:
        try:
            nxt = start.replace(year=start.year + max_years)
        except ValueError:  # Feb 29 -> Feb 28
            nxt = start.replace(year=start.year + max_years, day=28)
        end = min(until, nxt - timedelta(days=1))
        out.append((start, end))
        start = end + timedelta(days=1)
    return out
