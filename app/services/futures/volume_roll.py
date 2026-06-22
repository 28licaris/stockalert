"""Volume-based continuous-root construction for futures (pure logic).

Builds a continuous front-month series (/ES, /CL, …) from per-contract raw bars
in `futures.polygon_raw` — with NO REST and NO contract-metadata dependency.
Contract chronological order is parsed straight from the ticker month-code
(ESH4 → ESM4 → ESU4 → ESZ4 → ESH5 …), and the active contract each day is the
one carrying the most volume, with hysteresis so it never flip-flops.

This replaces the old contract_chain.py roll (fixed 4-days-before-expiry on a
REST-discovered chain), which broke on monthly roots. Volume rolls track where
liquidity actually is and are immune to the strip/pseudo-contract pollution.

Two stages:
  1. front_month_schedule(): {trading_day -> active contract}, monotonic in
     contract order, switching only when the next contract out-volumes the
     current for `hysteresis_days` consecutive sessions.
  2. ratio_back_adjust(): stitch the active contract's bars and remove the price
     gap at each roll by scaling history with a cumulative ratio. The front
     (most recent) segment keeps real prices (adj_factor=1.0); older bars carry
     adj_factor so the raw contract price is recoverable.

Why ratio (not difference) adjustment: preserves percentage moves, never goes
negative over long histories — the robust choice for indicators / Elliott Wave.
"""
from __future__ import annotations

from datetime import date

# CME/standard futures delivery-month codes → calendar month.
MONTH_CODE: dict[str, int] = {
    "F": 1, "G": 2, "H": 3, "J": 4, "K": 5, "M": 6,
    "N": 7, "Q": 8, "U": 9, "V": 10, "X": 11, "Z": 12,
}


def contract_sort_key(contract: str, root: str) -> tuple[int, int] | None:
    """Chronological key (year, month) for an outright contract ticker.

    ``contract_sort_key("ESH4", "ES") -> (2024, 3)``. Handles 1-digit (near,
    ESH4) and 2-digit (deferred, CLF30) year encodings. Returns None if the
    suffix isn't a valid <month-code><year>.
    """
    if not contract.startswith(root):
        return None
    suffix = contract[len(root):]
    if len(suffix) < 2:
        return None
    month = MONTH_CODE.get(suffix[0])
    yr = suffix[1:]
    if month is None or not yr.isdigit():
        return None
    if len(yr) == 1:
        # 1..9 -> 2021..2029, 0 -> 2030 (the entitled window is 2021-2026; the
        # deferred far months use 2-digit encoding, handled below).
        y = int(yr)
        year = 2030 if y == 0 else 2020 + y
    else:
        year = 2000 + int(yr)
    return (year, month)


def front_month_schedule(
    daily_volume: dict[date, dict[str, float]],
    *,
    hysteresis_days: int = 3,
    root: str,
) -> dict[date, str]:
    """Map each trading day to its active (front-month) contract.

    `daily_volume[day][contract] = total volume`. The active contract advances
    monotonically through contract order; it rolls to the next contract only
    after that contract out-volumes the current one for `hysteresis_days`
    consecutive sessions (avoids a one-day volume spike triggering a roll).

    Returns {day -> contract}. Days before any volume are omitted.
    """
    days = sorted(daily_volume)
    if not days:
        return {}

    def order(c: str):
        return contract_sort_key(c, root) or (9999, 99)

    schedule: dict[date, str] = {}
    current: str | None = None
    streak_candidate: str | None = None
    streak = 0

    for d in days:
        vols = daily_volume[d]
        if not vols:
            if current is not None:
                schedule[d] = current
            continue

        top = max(vols, key=lambda c: vols[c])  # highest-volume contract today

        if current is None:
            current = top
            schedule[d] = current
            continue

        # Only consider rolling FORWARD (to a later contract) and only if the
        # top contract today out-volumes the current one.
        if order(top) > order(current) and vols.get(top, 0) > vols.get(current, 0):
            if streak_candidate == top:
                streak += 1
            else:
                streak_candidate = top
                streak = 1
            if streak >= hysteresis_days:
                current = top
                streak_candidate = None
                streak = 0
        else:
            streak_candidate = None
            streak = 0

        schedule[d] = current

    return schedule


def roll_days(schedule: dict[date, str]) -> list[date]:
    """The days on which the active contract changes (first day of each new
    front month, excluding the very first)."""
    out: list[date] = []
    prev: str | None = None
    for d in sorted(schedule):
        c = schedule[d]
        if prev is not None and c != prev:
            out.append(d)
        prev = c
    return out
