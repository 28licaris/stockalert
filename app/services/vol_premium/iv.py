"""Option chain → one at-the-money implied volatility. Pure.

Rows in, a number out. No IO, no config, no ClickHouse — so the estimator that
half the product depends on is testable against hand-built chains.

## Why this is more than "read the IV column"

Schwab reports an implied volatility per contract, and taking one of them at
face value would be wrong three separate ways:

* **Sentinels.** 1.1% of contracts carry `-999`, Schwab's "no value". Read as a
  number it is a -999% volatility.
* **Solver blowups.** 3.5% read above 300%. A deep out-of-the-money strike with
  a two-cent quote has almost no vega, so tiny price noise implies enormous
  volatility. The number is arithmetic, not information.
* **Skew.** A single contract's IV depends on where its strike sits. A lone
  out-of-the-money put is a reading of crash pricing, not of the level of
  volatility. Averaging calls and puts across a narrow band around spot is the
  standard robust estimator, and requiring **both** is what stops an all-put
  average from quietly becoming a skew measurement.

Filtered rows are dropped with a reason. Nothing is repaired or substituted —
a screen with fewer honest rows beats one padded with invented ones.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime
from typing import Iterable, Optional, Sequence

from app.services.vol_premium.schemas import AtmImpliedVol, ScreenParams

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChainQuote:
    """One option contract, reduced to what the estimator needs.

    Deliberately not the full `OptionContractSnapshot`: this module should be
    callable from a test with four hand-written rows.
    """

    expiration_date: date
    days_to_expiration: int
    strike: float
    put_call: str                       # "CALL" / "PUT"
    implied_vol: Optional[float]        # percent, as Schwab reports it
    bid: Optional[float]
    ask: Optional[float]
    mark: Optional[float]
    open_interest: Optional[int]
    underlying_price: Optional[float]
    snapshot_ts: datetime

    @property
    def is_call(self) -> bool:
        return self.put_call.upper().startswith("C")

    def rel_spread(self) -> Optional[float]:
        """`(ask - bid) / mark`, the width of the market as a fraction of price.

        Falls back to the bid-ask midpoint when `mark` is missing, because a
        spread relative to nothing is not a number.
        """
        if self.bid is None or self.ask is None or self.ask < self.bid:
            return None
        ref = self.mark if self.mark else (self.bid + self.ask) / 2.0
        if not ref or ref <= 0:
            return None
        return (self.ask - self.bid) / ref


def _quality_filter(
    quotes: Iterable[ChainQuote], spot: float, params: ScreenParams
) -> tuple[list[ChainQuote], dict[str, int]]:
    """Keep the quotable, near-the-money contracts. Count every rejection."""
    kept: list[ChainQuote] = []
    rejected: dict[str, int] = defaultdict(int)

    for q in quotes:
        iv = q.implied_vol
        if iv is None or not (params.iv_min <= iv <= params.iv_max):
            # Catches the -999 sentinel and the solver blowups in one test.
            rejected["iv_out_of_range"] += 1
            continue
        if q.bid is None or q.bid <= 0 or q.ask is None or q.ask <= q.bid:
            rejected["no_two_sided_market"] += 1
            continue
        if (q.open_interest or 0) < params.min_open_interest:
            rejected["thin_open_interest"] += 1
            continue
        rs = q.rel_spread()
        if rs is None or rs > params.max_rel_spread:
            rejected["wide_spread"] += 1
            continue
        if spot <= 0 or abs(q.strike / spot - 1.0) > params.atm_band:
            rejected["outside_atm_band"] += 1
            continue
        kept.append(q)

    return kept, dict(rejected)


def _pick_expiry(
    quotes: Sequence[ChainQuote], params: ScreenParams
) -> Optional[date]:
    """The expiry closest to `target_dte` within `[min_dte, max_dte]`.

    Ties break toward the *longer* expiry: it is the more liquid side of a
    near-dated tie and avoids the last few days to expiry, where implied
    volatility is dominated by pin risk rather than by any view on the stock.
    """
    eligible = {
        q.expiration_date: q.days_to_expiration
        for q in quotes
        if params.min_dte <= q.days_to_expiration <= params.max_dte
    }
    if not eligible:
        return None
    return min(
        eligible, key=lambda e: (abs(eligible[e] - params.target_dte), -eligible[e])
    )


def atm_implied_vol(
    quotes: Sequence[ChainQuote],
    *,
    spot: float,
    params: Optional[ScreenParams] = None,
) -> tuple[Optional[AtmImpliedVol], dict[str, int]]:
    """At-the-money implied volatility for the target expiry.

    Returns `(result_or_None, rejection_counts)`. The counts come back either
    way so a caller can say *why* a symbol produced nothing — an empty screen
    row and a screen row that was never attempted are different facts.
    """
    params = params or ScreenParams()

    if spot <= 0:
        return None, {"bad_spot": 1}

    kept, rejected = _quality_filter(quotes, spot, params)
    if not kept:
        return None, rejected

    expiry = _pick_expiry(kept, params)
    if expiry is None:
        rejected["no_expiry_in_window"] = len(kept)
        return None, rejected

    at_expiry = [q for q in kept if q.expiration_date == expiry]
    calls = sum(1 for q in at_expiry if q.is_call)
    puts = len(at_expiry) - calls

    if len(at_expiry) < params.min_contracts:
        rejected["too_few_contracts"] = len(at_expiry)
        return None, rejected
    if calls == 0 or puts == 0:
        # An all-call or all-put average measures skew, not the level.
        rejected["one_sided_expiry"] = len(at_expiry)
        return None, rejected

    ivs = sorted(q.implied_vol for q in at_expiry)  # type: ignore[misc]
    spreads = sorted(s for s in (q.rel_spread() for q in at_expiry) if s is not None)

    return (
        AtmImpliedVol(
            expiration_date=expiry,
            days_to_expiration=at_expiry[0].days_to_expiration,
            implied_vol=float(sum(ivs) / len(ivs)),
            contracts_used=len(at_expiry),
            calls=calls,
            puts=puts,
            median_rel_spread=_median(spreads),
            snapshot_ts=max(q.snapshot_ts for q in at_expiry),
        ),
        rejected,
    )


def _median(values: Sequence[float]) -> float:
    if not values:
        return 0.0
    n = len(values)
    mid = n // 2
    return float(values[mid] if n % 2 else (values[mid - 1] + values[mid]) / 2.0)
