"""
Market session calendar — the single source of "is the market open?".

Backed by the maintained ``exchange_calendars`` library (no hand-kept holiday
table that drifts and rots — see docs/market_calendar_spec.md):

  - equities → ``XNYS`` (NYSE/Nasdaq): holidays, half-days, early closes.
  - futures  → ``CMES`` (CME Globex): Sun–Fri sessions + CME holidays.

Used by (a) gap detection — replacing the weekday()<5 heuristic so holidays
aren't mistaken for missing data (Juneteenth: equities closed, futures open),
and (b) the calendar API/frontend.

All session reasoning is in ET. Calendars are built once per process (lazy +
cached — construction is ~100 ms).
"""
from __future__ import annotations

from datetime import date, time, timedelta
from functools import lru_cache
from typing import Literal, Optional
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")

AssetClass = Literal["equities", "futures"]
_CAL_NAME = {"equities": "XNYS", "futures": "CMES"}

# status values returned by day_status / calendar_range
STATUS_OPEN = "open"
STATUS_CLOSED = "closed"
STATUS_EARLY_CLOSE = "early_close"


@lru_cache(maxsize=4)
def _cal(name: str):
    import exchange_calendars as xcals
    return xcals.get_calendar(name)


def _name(asset_class: AssetClass) -> str:
    try:
        return _CAL_NAME[asset_class]
    except KeyError:
        raise ValueError(
            f"unknown asset_class {asset_class!r}; expected one of {list(_CAL_NAME)}"
        )


def _ts(d: date):
    import pandas as pd
    return pd.Timestamp(d)


# ─────────────────────────────────────────────────────────────────────
# Session predicates (used by gap detection)
# ─────────────────────────────────────────────────────────────────────

def is_session(asset_class: AssetClass, d: date) -> bool:
    """True if `d` is a trading session for the asset class."""
    return bool(_cal(_name(asset_class)).is_session(_ts(d)))


def sessions(asset_class: AssetClass, start: date, end: date) -> list[date]:
    """Trading session dates in `[start, end]` (inclusive), chronological."""
    if start > end:
        return []
    idx = _cal(_name(asset_class)).sessions_in_range(_ts(start), _ts(end))
    return [ts.date() for ts in idx]


# Convenience aliases mirroring the spec's signatures.
def is_equities_session(d: date) -> bool:
    return is_session("equities", d)


def is_futures_session(d: date) -> bool:
    return is_session("futures", d)


def equities_sessions(start: date, end: date) -> list[date]:
    return sessions("equities", start, end)


def futures_sessions(start: date, end: date) -> list[date]:
    return sessions("futures", start, end)


# ─────────────────────────────────────────────────────────────────────
# Early closes + holiday names (used by the calendar API)
# ─────────────────────────────────────────────────────────────────────

def early_close_et(asset_class: AssetClass, d: date) -> Optional[time]:
    """ET close time if `d` is an early-close session, else None (and None
    if `d` isn't a session at all)."""
    cal = _cal(_name(asset_class))
    if not cal.is_session(_ts(d)):
        return None
    if _ts(d) not in cal.early_closes:
        return None
    close = cal.schedule.loc[_ts(d), "close"]
    return close.tz_convert(_ET).time()


@lru_cache(maxsize=64)
def _named_holidays(name: str, year: int) -> dict:
    """{date: holiday_name} for `year` — the library's rule-based holidays
    (adhoc/unnamed closures fall back to a generic reason)."""
    import pandas as pd
    cal = _cal(name)
    ser = cal.regular_holidays.holidays(
        pd.Timestamp(year, 1, 1), pd.Timestamp(year, 12, 31), return_name=True
    )
    return {ts.date(): str(n) for ts, n in ser.items()}


def closed_reason(asset_class: AssetClass, d: date) -> Optional[str]:
    """Why `d` is closed: the holiday name, 'Weekend', or None (None means
    it's actually a session)."""
    if is_session(asset_class, d):
        return None
    name = _named_holidays(_name(asset_class), d.year).get(d)
    if name:
        return name
    return "Weekend" if d.weekday() >= 5 else "Holiday"


# ─────────────────────────────────────────────────────────────────────
# Calendar view (used by the API/frontend)
# ─────────────────────────────────────────────────────────────────────

def day_status(asset_class: AssetClass, d: date) -> dict:
    """One day's status dict: {date, status, early_close_et, reason}.

    `status` ∈ open | early_close | closed. `early_close_et` is "HH:MM" ET
    on early-close days else None. `reason` is the holiday/weekend name on
    closed days else None.
    """
    name = _name(asset_class)
    cal = _cal(name)
    if cal.is_session(_ts(d)):
        ec = early_close_et(asset_class, d)
        return {
            "date": d,
            "status": STATUS_EARLY_CLOSE if ec else STATUS_OPEN,
            "early_close_et": ec.strftime("%H:%M") if ec else None,
            "reason": None,
        }
    return {
        "date": d,
        "status": STATUS_CLOSED,
        "early_close_et": None,
        "reason": closed_reason(asset_class, d),
    }


def calendar_range(asset_class: AssetClass, start: date, end: date) -> list[dict]:
    """Every CALENDAR day in `[start, end]` with its status (not just
    sessions) — so a frontend grid can render closed days too."""
    _name(asset_class)  # validate
    out: list[dict] = []
    d = start
    while d <= end:
        out.append(day_status(asset_class, d))
        d += timedelta(days=1)
    return out
