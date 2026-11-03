"""VolPremiumService — chains and bars in, a ranked table out.

The orchestrator. Everything it calls is pure; this is the only file in the
module that touches ClickHouse.

Pipeline, per symbol:

    latest chain  → quality filter → ATM implied vol at the target expiry
    daily closes  → GJR-GARCH fit  → forecast vol at THAT expiry's horizon
                  → ratio + points → one row

Both data sources are injected, so the whole screen runs on hand-built inputs
with no database (`docs/standards/service_modules.md`).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Callable, Optional, Sequence

import numpy as np

from app.services.ppl.garch import GarchFitError
from app.services.vol_premium import forecast as fc
from app.services.vol_premium.history import percentile_rank
from app.services.vol_premium.iv import ChainQuote, atm_implied_vol
from app.services.vol_premium.schemas import (
    MODEL_VERSION,
    ScreenParams,
    SkippedSymbol,
    VolPremiumRow,
    VolPremiumRowResult,
    VolPremiumScreen,
)

logger = logging.getLogger(__name__)

ChainsFn = Callable[[Sequence[str]], dict[str, list[ChainQuote]]]
HistoryFn = Callable[[Sequence[str]], dict[str, list[float]]]
ClosesFn = Callable[[str, int], np.ndarray]
UniverseFn = Callable[[], list[str]]


class VolPremiumService:
    """Builds the ranked screen."""

    def __init__(
        self,
        *,
        chains_fn: ChainsFn,
        closes_fn: ClosesFn,
        universe_fn: Optional[UniverseFn] = None,
        history_fn: Optional[HistoryFn] = None,
    ) -> None:
        self._chains_fn = chains_fn
        self._closes_fn = closes_fn
        self._universe_fn = universe_fn
        # Optional: without it rows still build, just with no percentile. A
        # missing baseline must degrade the reading, not break the screen.
        self._history_fn = history_fn

    @classmethod
    def from_settings(cls) -> "VolPremiumService":
        from app.services.vol_premium.sources import (
            load_chains,
            load_closes,
            load_premium_history,
            options_universe,
        )

        return cls(
            chains_fn=load_chains,
            closes_fn=load_closes,
            universe_fn=options_universe,
            history_fn=load_premium_history,
        )

    # ── public ────────────────────────────────────────────────────────

    def build_row(
        self, symbol: str, params: Optional[ScreenParams] = None
    ) -> VolPremiumRowResult:
        params = params or ScreenParams()
        symbol = symbol.upper().strip()
        chains = self._chains_fn([symbol])
        hist = self._history_fn([symbol]) if self._history_fn else {}
        return self._row(symbol, chains.get(symbol, []), params,
                         history=hist.get(symbol, []))

    def build_screen(
        self,
        symbols: Optional[Sequence[str]] = None,
        params: Optional[ScreenParams] = None,
    ) -> VolPremiumScreen:
        params = params or ScreenParams()

        if symbols is None:
            if self._universe_fn is None:
                raise ValueError(
                    "no symbols given and this service has no universe source"
                )
            symbols = self._universe_fn()
        symbols = [s.upper().strip() for s in symbols if s and s.strip()]

        chains = self._chains_fn(symbols)
        hist = self._history_fn(symbols) if self._history_fn else {}

        rows: list[VolPremiumRow] = []
        skipped: list[SkippedSymbol] = []
        for sym in symbols:
            res = self._row(sym, chains.get(sym, []), params,
                            history=hist.get(sym, []))
            if res.ok and res.row is not None:
                rows.append(res.row)
            else:
                skipped.append(
                    SkippedSymbol(symbol=sym, status=res.status, reason=res.reason or "")
                )

        # Most expensive first — the answer to "what should I look at today".
        rows.sort(key=lambda r: r.vrp_ratio, reverse=True)

        logger.info(
            "vol_premium: %d/%d rows built (%d skipped)",
            len(rows), len(symbols), len(skipped),
        )
        return VolPremiumScreen(
            as_of=datetime.now(timezone.utc),
            rows=rows,
            skipped=skipped,
            params=params,
            model_version=MODEL_VERSION,
        )

    # ── internals ─────────────────────────────────────────────────────

    def _row(
        self, symbol: str, quotes: list[ChainQuote], params: ScreenParams,
        *, history: Optional[Sequence[float]] = None,
    ) -> VolPremiumRowResult:
        if not quotes:
            return VolPremiumRowResult(
                status="no_chain", reason="no option contracts for this symbol"
            )

        now = datetime.now(timezone.utc)
        newest = max(q.snapshot_ts for q in quotes)
        age_h = (now - _utc(newest)).total_seconds() / 3600.0
        if age_h > params.freshness_hours:
            # A chain this old is not a picture of today's market, and the
            # screen's whole premise is "today". Reported, never used anyway.
            return VolPremiumRowResult(
                status="stale",
                reason=f"newest chain snapshot is {age_h:.0f}h old "
                       f"(limit {params.freshness_hours}h)",
            )

        spot = _spot(quotes)
        if spot is None:
            return VolPremiumRowResult(
                status="no_chain", reason="chain carries no underlying price"
            )

        atm, rejected = atm_implied_vol(quotes, spot=spot, params=params)
        if atm is None:
            return VolPremiumRowResult(
                status="no_chain",
                reason="no contract survived the quality filter: "
                       + (", ".join(f"{k}={v}" for k, v in sorted(rejected.items()))
                          or "empty chain"),
            )

        closes = self._closes_fn(symbol, params.fit_lookback_bars)
        if closes is None or len(closes) < 2:
            return VolPremiumRowResult(
                status="no_forecast", reason="no daily bars for this symbol"
            )

        try:
            vol, fit, trading_days = fc.forecast_vol_for_expiry(
                closes, calendar_days=atm.days_to_expiration
            )
        except (GarchFitError, ValueError) as exc:
            # The forecast IS the denominator. Without a trustworthy one there
            # is no honest ratio, so the symbol drops out rather than being
            # ranked on a weaker estimate wearing the same name.
            return VolPremiumRowResult(status="no_forecast", reason=str(exc))

        ratio, points = fc.vol_premium(atm.implied_vol, vol)
        # Where this ratio sits in the symbol's OWN history — the reading that
        # makes a bare ratio actionable. None when no baseline exists.
        pct = percentile_rank(ratio, history or [])

        return VolPremiumRowResult(
            status="ok",
            row=VolPremiumRow(
                symbol=symbol,
                as_of=now,
                spot=spot,
                expiration_date=atm.expiration_date,
                days_to_expiration=atm.days_to_expiration,
                trading_days=trading_days,
                implied_vol=round(atm.implied_vol, 3),
                forecast_vol=round(vol, 3),
                vrp_ratio=round(ratio, 4),
                vrp_points=round(points, 3),
                premium_percentile=(
                    None if pct is None else round(pct, 1)
                ),
                baseline_days=len(history or []),
                contracts_used=atm.contracts_used,
                calls=atm.calls,
                puts=atm.puts,
                median_rel_spread=round(atm.median_rel_spread, 4),
                snapshot_age_hours=round(age_h, 2),
                garch_sigma_next=fit.sigma_next,
                garch_persistence=fit.persistence,
                variance_ratio=round(fit.variance_ratio(trading_days), 4),
                fit_bars=fit.n_obs,
            ),
        )


def _utc(ts: datetime) -> datetime:
    return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)


def _spot(quotes: Sequence[ChainQuote]) -> Optional[float]:
    """Underlying price from the freshest contract that carries one."""
    priced = [q for q in quotes if q.underlying_price and q.underlying_price > 0]
    if not priced:
        return None
    return float(max(priced, key=lambda q: q.snapshot_ts).underlying_price)
