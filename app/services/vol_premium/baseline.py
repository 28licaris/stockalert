"""Reduce raw end-of-day option chains to one at-the-money number per day. Pure.

## Why this exists

The screen's cross-sectional reading ("SNOW is at 1.37x") is weak on its own —
a positive premium is normal, so a bare ratio cannot say whether today is
unusual. The reading that matters is against the symbol's **own** history:

    "SNOW's options are at their 96th percentile of the last ten years."

ThetaData gives us the raw material for that: end-of-day greeks back to 2016
for the whole options universe, with implied volatility and quotes populated.
What it does not give is anything small enough to query live — SPY alone is
~14,600 contract rows *per session*, so the full archive runs to hundreds of
millions of rows.

So this reduces it once. Per `(symbol, date)` we keep a single at-the-money
implied volatility at the target horizon: roughly 100,000 rows across ten years
and forty symbols, which makes a percentile lookup instant.

## The estimator is the same one the live screen uses

Deliberately. `iv.py` picks the expiry nearest 30 days, averages near-the-money
calls *and* puts, and refuses one-sided expiries. If the baseline were built by
a different rule, today's Schwab reading would be compared against a history
measured another way and the percentile would be meaningless — the comparison
is only valid if both sides are the same measurement.

This module is the vectorized twin of that logic: the same gates, applied to
millions of rows at once rather than one chain at a time. `test_baseline.py`
asserts the two agree on identical input, which is what keeps them honest.

## Units

ThetaData reports implied volatility as a **decimal** (0.2141); Schwab reports
**percent** (21.41). Output here is percent, matching the live screen — a
missed conversion would silently misprice every comparison by 100x.
"""
from __future__ import annotations

import logging
from datetime import date

import polars as pl

from app.services.vol_premium.schemas import ScreenParams

logger = logging.getLogger(__name__)

# ThetaData's implied_vol is a decimal fraction; the live path is in percent.
THETA_IV_TO_PERCENT = 100.0

OUTPUT_COLUMNS = [
    "symbol", "eod_date", "expiration_date", "days_to_expiration",
    "atm_iv", "contracts", "calls", "puts", "underlying_price",
]


def reduce_eod_chains(
    frame: pl.DataFrame,
    *,
    params: ScreenParams | None = None,
    iv_is_decimal: bool = True,
) -> pl.DataFrame:
    """Collapse raw EOD contract rows to one ATM implied vol per (symbol, date).

    `frame` needs: `underlying_symbol`, `eod_date`, `expiration_date`, `strike`,
    `put_call`, `implied_vol`, `underlying_price`.

    Returns `OUTPUT_COLUMNS`, with `atm_iv` in percent. Rows that fail any gate
    are dropped rather than patched — a day with no honest ATM reading must be
    absent from the baseline, not represented by a guess.
    """
    params = params or ScreenParams()

    if frame.is_empty():
        return pl.DataFrame(schema={c: pl.Utf8 for c in OUTPUT_COLUMNS}).clear()

    scale = THETA_IV_TO_PERCENT if iv_is_decimal else 1.0

    df = (
        frame.lazy()
        .rename({"underlying_symbol": "symbol"})
        .with_columns(
            (pl.col("implied_vol") * scale).alias("iv_pct"),
            (pl.col("expiration_date") - pl.col("eod_date")).dt.total_days()
            .cast(pl.Int32).alias("dte"),
        )
        # Same gates as the live estimator: sane IV (this also removes provider
        # sentinels and solver blowups), a usable horizon, and near-the-money.
        .filter(
            pl.col("iv_pct").is_not_null()
            & pl.col("iv_pct").is_between(params.iv_min, params.iv_max)
            & pl.col("underlying_price").is_not_null()
            & (pl.col("underlying_price") > 0)
            & pl.col("dte").is_between(params.min_dte, params.max_dte)
            & (
                (pl.col("strike") / pl.col("underlying_price") - 1.0).abs()
                <= params.atm_band
            )
        )
    )

    # Pick one expiry per (symbol, date): closest to target, ties to the LONGER
    # one — same rule as `iv.py._pick_expiry`, and for the same reason (the
    # near-dated side of a tie is dominated by pin risk).
    chosen = (
        df.with_columns(
            ((pl.col("dte") - params.target_dte).abs()).alias("dist")
        )
        .sort(["symbol", "eod_date", "dist", "dte"], descending=[False, False, False, True])
        .group_by(["symbol", "eod_date"], maintain_order=True)
        .agg(pl.col("expiration_date").first().alias("expiration_date"))
    )

    out = (
        df.join(chosen, on=["symbol", "eod_date", "expiration_date"], how="inner")
        .group_by(["symbol", "eod_date", "expiration_date"])
        .agg(
            pl.col("iv_pct").mean().alias("atm_iv"),
            pl.len().alias("contracts"),
            (pl.col("put_call").str.to_uppercase().str.starts_with("C"))
            .sum().cast(pl.Int32).alias("calls"),
            pl.col("dte").first().alias("days_to_expiration"),
            pl.col("underlying_price").first().alias("underlying_price"),
        )
        .with_columns((pl.col("contracts") - pl.col("calls")).alias("puts"))
        # Enough support, and BOTH sides — an all-put average measures crash
        # pricing, not the level of volatility.
        .filter(
            (pl.col("contracts") >= params.min_contracts)
            & (pl.col("calls") > 0)
            & (pl.col("puts") > 0)
        )
        .select(OUTPUT_COLUMNS)
        .sort(["symbol", "eod_date"])
        .collect()
    )

    logger.debug("baseline: %d raw rows -> %d daily ATM rows", frame.height, out.height)
    return out


def percentile_of(value: float, history: list[float] | pl.Series) -> float | None:
    """Where `value` sits in `history`, as a percentile in [0, 100].

    The number that turns "SNOW is at 1.37x" into "SNOW is at its 96th
    percentile" — the difference between a curiosity and a reason to look.

    Returns None on an empty history rather than a default, because "no
    baseline" and "unremarkable" are opposite claims and must not share a value.
    """
    s = pl.Series(history) if not isinstance(history, pl.Series) else history
    s = s.drop_nulls()
    if s.len() == 0:
        return None
    return float((s < value).sum() / s.len() * 100.0)


def date_window(start: date, end: date) -> tuple[date, date]:
    """Validated inclusive window, so a reversed range fails loudly."""
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    return start, end
