"""CME futures contract chain — front-month window assignment.

Two-phase API:
  1. discover_contracts(client, product_code, ...)
       Queries Polygon list_futures_contracts to get actual first/last
       trade dates per contract — no math, no hardcoded calendars.
  2. build_front_month_windows(contracts)
       Assigns non-overlapping front-month windows using the standard
       CME roll convention: a contract stops being front month
       ROLL_DAYS_BEFORE_EXPIRY business days before its last trade date.

This is the ground truth for which contract's bars represent the
continuous root (/ES) on any given day. Downstream consumers (the
backfill script, the nightly job) iterate the returned windows and pull
bars for each (contract, window) pair — no look-ahead, no gap.

Supported product codes (Polygon uses codes without slash):
  ES, NQ, YM, RTY, GC, SI, HG, CL, NG, ZB, ZN, 6E, 6J, etc.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# CME standard roll: 4 business days before last trade date.
# This matches the typical rollover in open interest for ES/NQ/RTY/YM.
# Override per-product if you discover a different convention later.
ROLL_DAYS_BEFORE_EXPIRY = 4


@dataclass(frozen=True, order=True)
class ContractInfo:
    """Raw Polygon contract metadata — one row per listed contract."""
    ticker: str           # "ESZ5", "ESH6", …
    product_code: str     # "ES" (no slash)
    first_trade_date: date
    last_trade_date: date


@dataclass(frozen=True)
class ContractWindow:
    """A contract's exclusive front-month date window.

    Pull bars for `ticker` within [front_start, front_end] and label
    them with `symbol` (the continuous root)."""
    ticker: str           # "ESZ5"
    product_code: str     # "ES"
    symbol: str           # "/ES"  — the continuous root to store
    front_start: date     # inclusive
    front_end: date       # inclusive (= roll_date)
    last_trade_date: date


# ── Public API ──────────────────────────────────────────────────────────────


def discover_contracts(
    client,
    product_code: str,
    *,
    start_date: Optional[date] = None,
    end_date: Optional[date] = None,
) -> list[ContractInfo]:
    """Query Polygon for all contracts for `product_code` (e.g. "ES").

    Filters to contracts whose date range overlaps [start_date, end_date]
    so you don't pull metadata for contracts outside your backfill window.
    Returns sorted by ticker (= chronological for standard quarterly codes).

    The massive SDK paginates automatically; this materialises the full list.
    """
    kwargs: dict = {
        "product_code": product_code,
        "sort": "ticker",
        "limit": 250,
    }
    if start_date:
        # Include contracts that ended on or after start_date
        kwargs["last_trade_date_gte"] = start_date.isoformat()
    if end_date:
        # Include contracts that started on or before end_date
        kwargs["first_trade_date_lte"] = end_date.isoformat()

    contracts: list[ContractInfo] = []
    seen: set[str] = set()
    try:
        for fc in client.list_futures_contracts(**kwargs):
            ticker = (getattr(fc, "ticker", None) or "").strip()
            if not ticker or ticker in seen:
                continue
            seen.add(ticker)

            ftd = _parse_date(getattr(fc, "first_trade_date", None))
            ltd = _parse_date(getattr(fc, "last_trade_date", None))
            if ftd is None or ltd is None:
                logger.debug("discover_contracts: %s missing dates, skip", ticker)
                continue

            contracts.append(ContractInfo(
                ticker=ticker,
                product_code=product_code.upper(),
                first_trade_date=ftd,
                last_trade_date=ltd,
            ))
    except Exception as exc:
        logger.error("discover_contracts: %s — API error: %s", product_code, exc)
        raise

    contracts.sort(key=lambda c: c.ticker)
    logger.info(
        "discover_contracts: %s → %d contracts (%s → %s)",
        product_code,
        len(contracts),
        contracts[0].first_trade_date if contracts else "–",
        contracts[-1].last_trade_date if contracts else "–",
    )
    return contracts


def build_front_month_windows(
    contracts: list[ContractInfo],
) -> list[ContractWindow]:
    """Assign non-overlapping front-month windows to each contract.

    Algorithm:
      • Contract[0]: front_start = first_trade_date, front_end = roll_date(0)
      • Contract[i]: front_start = roll_date(i-1) + 1 calendar day,
                     front_end   = roll_date(i)
      • roll_date    = last_trade_date - ROLL_DAYS_BEFORE_EXPIRY business days

    Windows are guaranteed non-overlapping and cover the full calendar
    span without gaps (calendar days, not trading days, between rolls).
    """
    if not contracts:
        return []

    windows: list[ContractWindow] = []
    prev_roll: Optional[date] = None

    for c in contracts:
        rd = _roll_date(c.last_trade_date)
        front_start = (prev_roll + timedelta(days=1)) if prev_roll else c.first_trade_date

        if front_start > rd:
            # Degenerate contract or extreme overlap — skip
            logger.warning(
                "contract_chain: %s window collapsed (start=%s > roll=%s); skipping",
                c.ticker, front_start, rd,
            )
            prev_roll = rd
            continue

        windows.append(ContractWindow(
            ticker=c.ticker,
            product_code=c.product_code,
            symbol=f"/{c.product_code}",
            front_start=front_start,
            front_end=rd,
            last_trade_date=c.last_trade_date,
        ))
        prev_roll = rd

    logger.info(
        "build_front_month_windows: %d windows (%s → %s)",
        len(windows),
        windows[0].front_start if windows else "–",
        windows[-1].front_end if windows else "–",
    )
    return windows


# ── Helpers ─────────────────────────────────────────────────────────────────


def _roll_date(last_trade_date: date) -> date:
    """The last day this contract is treated as front month.

    = last_trade_date minus ROLL_DAYS_BEFORE_EXPIRY business days."""
    return _sub_business_days(last_trade_date, ROLL_DAYS_BEFORE_EXPIRY)


def _sub_business_days(d: date, n: int) -> date:
    """Subtract n business days (Mon-Fri) from d."""
    count = 0
    cur = d
    while count < n:
        cur -= timedelta(days=1)
        if cur.weekday() < 5:  # Mon=0 … Fri=4
            count += 1
    return cur


def _parse_date(val) -> Optional[date]:
    """Parse a date from a string, date, or datetime. Returns None on failure."""
    if val is None:
        return None
    if isinstance(val, date):
        return val if not hasattr(val, "date") else val.date()  # type: ignore[attr-defined]
    try:
        return date.fromisoformat(str(val)[:10])
    except Exception:
        logger.debug("contract_chain: could not parse date %r", val)
        return None
