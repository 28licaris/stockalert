"""ClickHouse loaders for the Vol Premium screen.

Isolated from `service.py` so the screen itself stays injectable and testable
without a database. Nothing here computes anything — it only reshapes rows.
"""
from __future__ import annotations

import logging
from typing import Sequence

import numpy as np

from app.services.vol_premium.iv import ChainQuote

logger = logging.getLogger(__name__)

CONTRACTS_TABLE = "options_contracts_latest"
DAILY_TABLE = "ohlcv_daily"


def options_universe() -> list[str]:
    """Underlyings the options ingest currently covers.

    Read from the data rather than from a config list, so the screen can never
    claim coverage of a symbol whose chain nobody is collecting.
    """
    from app.db.client import get_client

    rows = get_client().query(
        f"SELECT DISTINCT underlying_symbol FROM {CONTRACTS_TABLE} ORDER BY 1"
    ).result_rows
    return [r[0] for r in rows]


def load_chains(symbols: Sequence[str]) -> dict[str, list[ChainQuote]]:
    """Latest chain for each symbol, as `ChainQuote` rows.

    One query for the whole universe rather than one per symbol: 40 round trips
    to fetch ~90k rows is strictly slower than one.

    Only the columns the estimator needs are selected, and no filtering happens
    here — quality gates live in `iv.py` where they are unit-tested and where
    their rejection counts can be reported.
    """
    if not symbols:
        return {}

    from app.db.client import get_client

    rows = get_client().query(
        f"""
        SELECT underlying_symbol, expiration_date, days_to_expiration, strike,
               put_call, volatility, bid, ask, mark, open_interest,
               underlying_price, snapshot_ts
        FROM {CONTRACTS_TABLE} FINAL
        WHERE underlying_symbol IN {{syms:Array(String)}}
          AND days_to_expiration IS NOT NULL
        """,
        parameters={"syms": list(symbols)},
    ).result_rows

    out: dict[str, list[ChainQuote]] = {s: [] for s in symbols}
    for (sym, exp, dte, strike, pc, iv, bid, ask, mark, oi, ulp, ts) in rows:
        out.setdefault(sym, []).append(
            ChainQuote(
                expiration_date=exp,
                days_to_expiration=int(dte),
                strike=float(strike),
                put_call=str(pc),
                implied_vol=None if iv is None else float(iv),
                bid=None if bid is None else float(bid),
                ask=None if ask is None else float(ask),
                mark=None if mark is None else float(mark),
                open_interest=None if oi is None else int(oi),
                underlying_price=None if ulp is None else float(ulp),
                snapshot_ts=ts,
            )
        )

    logger.debug("vol_premium: loaded %d contracts for %d symbols", len(rows), len(symbols))
    return out


def load_closes(symbol: str, limit: int) -> np.ndarray:
    """The newest `limit` daily closes, oldest-first.

    Same source and same window as `/ppl` at 1d, so the two surfaces cannot
    report different volatility for the same symbol on the same day.
    """
    from app.db.client import get_client

    rows = get_client().query(
        f"""
        SELECT close FROM (
            SELECT timestamp, close FROM {DAILY_TABLE} FINAL
            WHERE symbol = {{s:String}}
            ORDER BY timestamp DESC
            LIMIT {{n:UInt32}}
        ) ORDER BY timestamp ASC
        """,
        parameters={"s": symbol.upper().strip(), "n": int(limit)},
    ).result_rows
    return np.array([float(r[0]) for r in rows], dtype="float64")
