"""
Market calendar events — free + production-robust (market_calendar_spec §12a).

Three source kinds, all free, none doing live HTML scraping:
  - **computed**  — OPEX / quad-witching, a pure function off the session
    calendar (can't break, no dependency).
  - **seeded**    — FOMC + macro from the committed data/market_events_seed.json.
  - **owned (CH)**— dividend/split ex-dates synced into the `market_events`
    ClickHouse table (Phase 2b). Read here cold-start-safe (empty if absent).

`events_in_range()` merges all three into one sorted list for the calendar API.
Each event is a plain dict: {event_date(date), event_time_et, symbol,
event_type, title, importance, source}.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, timedelta

logger = logging.getLogger(__name__)

_SEED_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
    "data", "market_events_seed.json",
)
_QUAD_MONTHS = {3, 6, 9, 12}


def _event(
    d: date, event_type: str, title: str, importance: str, source: str,
    *, event_time_et: str = "", symbol: str = "",
) -> dict:
    return {
        "event_date": d,
        "event_time_et": event_time_et,
        "symbol": symbol,
        "event_type": event_type,
        "title": title,
        "importance": importance,
        "source": source,
    }


# ─────────────────────────────────────────────────────────────────────
# Computed events (OPEX / quad-witching)
# ─────────────────────────────────────────────────────────────────────

def _third_friday(year: int, month: int) -> date:
    first = date(year, month, 1)
    first_friday = first + timedelta(days=(4 - first.weekday()) % 7)
    return first_friday + timedelta(days=14)


def computed_events(start: date, end: date) -> list[dict]:
    """OPEX (monthly 3rd Friday; quarterly = quad-witching). If the 3rd Friday
    is an exchange holiday, expiration moves to the prior session (standard
    rule). Pure — generated at read, never stored."""
    if start > end:
        return []
    from app.services.market_calendar import is_equities_session

    out: list[dict] = []
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        exp = _third_friday(y, m)
        # Holiday → preceding session.
        guard = 0
        while not is_equities_session(exp) and guard < 7:
            exp -= timedelta(days=1)
            guard += 1
        if start <= exp <= end:
            quad = m in _QUAD_MONTHS
            out.append(_event(
                exp,
                "quad_witching" if quad else "opex",
                "Quad witching" if quad else "Options expiration (OPEX)",
                "high" if quad else "medium",
                "computed",
                event_time_et="16:00",
            ))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


# ─────────────────────────────────────────────────────────────────────
# Seeded events (FOMC + macro) — committed file
# ─────────────────────────────────────────────────────────────────────

def seed_events(start: date, end: date, *, path: str | None = None) -> list[dict]:
    """Load committed macro events (FOMC, …) and filter to [start, end].
    Cold-start safe: a missing/invalid seed file logs a warning and yields []."""
    p = path or _SEED_PATH
    try:
        with open(p, "r") as f:
            raw = json.load(f)
    except Exception as e:  # noqa: BLE001 — boundary
        logger.warning("market_events: seed file unreadable (%s); no seeded events", e)
        return []
    out: list[dict] = []
    for row in raw.get("events", []):
        try:
            d = date.fromisoformat(row["date"])
        except Exception:
            logger.warning("market_events: bad seed row skipped: %r", row)
            continue
        if start <= d <= end:
            out.append(_event(
                d,
                row.get("event_type", "macro"),
                row.get("title", ""),
                row.get("importance", "medium"),
                row.get("source", "seed"),
                event_time_et=row.get("event_time_et", ""),
                symbol=row.get("symbol", ""),
            ))
    return out


# ─────────────────────────────────────────────────────────────────────
# Owned events from ClickHouse (dividend/split ex-dates — Phase 2b)
# ─────────────────────────────────────────────────────────────────────

def ch_events(start: date, end: date, *, symbol: str | None = None, client=None) -> list[dict]:
    """Read events from the CH `market_events` table (corp-actions sync lands
    here in Phase 2b). Cold-start safe: returns [] if the table is empty or
    unavailable — never raises into the API."""
    try:
        if client is None:
            from app.db.client import get_client
            client = get_client()
        params: dict = {"start": start, "end": end}
        where = "event_date >= %(start)s AND event_date <= %(end)s"
        if symbol:
            where += " AND symbol = %(symbol)s"
            params["symbol"] = symbol.upper()
        rows = client.query(
            f"SELECT event_date, event_time_et, symbol, event_type, title, "
            f"importance, source FROM market_events FINAL WHERE {where}",
            parameters=params,
        ).result_rows
    except Exception as e:  # noqa: BLE001 — boundary
        logger.warning("market_events: CH read failed (%s); no stored events", e)
        return []
    return [
        _event(r[0], r[3], r[4], r[5], r[6], event_time_et=r[1] or "", symbol=r[2] or "")
        for r in rows
    ]


# ─────────────────────────────────────────────────────────────────────
# Merge — what the calendar API consumes
# ─────────────────────────────────────────────────────────────────────

def events_in_range(start: date, end: date, *, symbol: str | None = None) -> list[dict]:
    """All events in [start, end] from every free source, sorted by
    (date, time, type). `symbol` filters owned/CH events; computed + macro
    (symbol-less) always pass through."""
    evts = computed_events(start, end) + seed_events(start, end)
    evts += ch_events(start, end, symbol=symbol)
    if symbol:
        # Keep market-wide events (no symbol) + this symbol's events.
        sym = symbol.upper()
        evts = [e for e in evts if not e["symbol"] or e["symbol"] == sym]
    evts.sort(key=lambda e: (e["event_date"], e["event_time_et"] or "", e["event_type"]))
    return evts
