"""The ATM implied-volatility estimator, against hand-built chains.

Each test encodes one way the raw `volatility` column lies. They are not
hypothetical: every filter here was written because the real Schwab data
required it (spec §3.1).
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from app.services.vol_premium.iv import ChainQuote, atm_implied_vol
from app.services.vol_premium.schemas import ScreenParams

TS = datetime(2026, 8, 7, 18, 0, tzinfo=timezone.utc)
SPOT = 100.0


def quote(
    *,
    strike: float = 100.0,
    iv: float | None = 25.0,
    dte: int = 30,
    put_call: str = "CALL",
    bid: float | None = 4.0,
    ask: float | None = 4.2,
    mark: float | None = 4.1,
    oi: int | None = 500,
    ts: datetime = TS,
) -> ChainQuote:
    return ChainQuote(
        expiration_date=date(2026, 9, 6) + timedelta(days=dte - 30),
        days_to_expiration=dte,
        strike=strike,
        put_call=put_call,
        implied_vol=iv,
        bid=bid,
        ask=ask,
        mark=mark,
        open_interest=oi,
        underlying_price=SPOT,
        snapshot_ts=ts,
    )


def balanced(n: int = 4, **kw) -> list[ChainQuote]:
    """A minimal healthy chain: equal calls and puts straddling spot."""
    out = []
    for i in range(n):
        side = "CALL" if i % 2 == 0 else "PUT"
        out.append(quote(strike=99.0 + i * 0.5, put_call=side, **kw))
    return out


# ── the happy path ────────────────────────────────────────────────────


def test_averages_calls_and_puts_across_the_atm_band():
    quotes = [
        quote(strike=99.0, put_call="CALL", iv=24.0),
        quote(strike=100.0, put_call="PUT", iv=26.0),
        quote(strike=101.0, put_call="CALL", iv=25.0),
        quote(strike=100.5, put_call="PUT", iv=25.0),
    ]
    res, rejected = atm_implied_vol(quotes, spot=SPOT)

    assert res is not None
    assert res.implied_vol == pytest.approx(25.0)
    assert (res.contracts_used, res.calls, res.puts) == (4, 2, 2)
    assert not rejected


def test_reports_spread_quality_and_snapshot_time():
    quotes = balanced(4, bid=4.0, ask=4.4, mark=4.2)
    res, _ = atm_implied_vol(quotes, spot=SPOT)

    assert res is not None
    # (4.4 - 4.0) / 4.2
    assert res.median_rel_spread == pytest.approx(0.4 / 4.2, rel=1e-6)
    assert res.snapshot_ts == TS


# ── the three ways the IV column lies (spec §3.1) ─────────────────────


def test_drops_the_minus_999_sentinel_rather_than_averaging_it():
    """Schwab writes -999 for "no value". Averaged in, one such contract drags
    a 25% reading to -231%."""
    quotes = balanced(4) + [quote(strike=100.0, put_call="PUT", iv=-999.0)]
    res, rejected = atm_implied_vol(quotes, spot=SPOT)

    assert res is not None
    assert res.implied_vol == pytest.approx(25.0), "sentinel must not reach the mean"
    assert rejected["iv_out_of_range"] == 1


def test_drops_solver_blowups_above_the_ceiling():
    """A two-cent far-OTM quote has almost no vega, so price noise implies
    enormous volatility. Arithmetic, not information."""
    quotes = balanced(4) + [quote(strike=100.5, put_call="CALL", iv=850.0)]
    res, rejected = atm_implied_vol(quotes, spot=SPOT)

    assert res is not None
    assert res.implied_vol == pytest.approx(25.0)
    assert rejected["iv_out_of_range"] == 1


def test_refuses_a_one_sided_expiry():
    """An all-call average measures skew, not the level of volatility."""
    quotes = [quote(strike=99.0 + i * 0.4, put_call="CALL") for i in range(5)]
    res, rejected = atm_implied_vol(quotes, spot=SPOT)

    assert res is None
    assert rejected["one_sided_expiry"] == 5


# ── liquidity ─────────────────────────────────────────────────────────


def test_requires_a_two_sided_market():
    """Before the quote-parser fix every bid was NULL. A row with no bid is not
    a tradeable price and must not back a ranking."""
    res, rejected = atm_implied_vol(balanced(4, bid=None, ask=None), spot=SPOT)

    assert res is None
    assert rejected["no_two_sided_market"] == 4


def test_drops_wide_markets():
    params = ScreenParams(max_rel_spread=0.25)
    wide = balanced(4, bid=1.0, ask=3.0, mark=2.0)      # 100% spread
    res, rejected = atm_implied_vol(wide, spot=SPOT, params=params)

    assert res is None
    assert rejected["wide_spread"] == 4


def test_drops_thin_open_interest():
    res, rejected = atm_implied_vol(balanced(4, oi=1), spot=SPOT)

    assert res is None
    assert rejected["thin_open_interest"] == 4


def test_drops_strikes_outside_the_atm_band():
    far = [quote(strike=140.0, put_call="CALL"), quote(strike=60.0, put_call="PUT")]
    res, rejected = atm_implied_vol(balanced(4) + far, spot=SPOT)

    assert res is not None
    assert res.contracts_used == 4
    assert rejected["outside_atm_band"] == 2


# ── expiry selection ──────────────────────────────────────────────────


def test_picks_the_expiry_nearest_the_target():
    quotes = balanced(4, dte=7) + balanced(4, dte=31) + balanced(4, dte=200)
    res, _ = atm_implied_vol(quotes, spot=SPOT, params=ScreenParams(target_dte=30))

    assert res is not None
    assert res.days_to_expiration == 31


def test_ignores_expiries_outside_the_window():
    """7 days is pin-risk territory and 200 is illiquid; neither should win
    just because nothing else is present."""
    quotes = balanced(4, dte=3) + balanced(4, dte=300)
    res, rejected = atm_implied_vol(quotes, spot=SPOT)

    assert res is None
    assert rejected["no_expiry_in_window"] == 8


def test_ties_break_toward_the_longer_expiry():
    """Equidistant from target, the longer one avoids expiry-week pin risk."""
    quotes = balanced(4, dte=25) + balanced(4, dte=35)
    res, _ = atm_implied_vol(quotes, spot=SPOT, params=ScreenParams(target_dte=30))

    assert res is not None
    assert res.days_to_expiration == 35


# ── degenerate inputs ─────────────────────────────────────────────────


def test_too_few_contracts_returns_nothing():
    quotes = [quote(strike=100.0, put_call="CALL"), quote(strike=100.0, put_call="PUT")]
    res, rejected = atm_implied_vol(quotes, spot=SPOT, params=ScreenParams(min_contracts=4))

    assert res is None
    assert rejected["too_few_contracts"] == 2


def test_empty_chain_and_bad_spot():
    assert atm_implied_vol([], spot=SPOT)[0] is None
    res, rejected = atm_implied_vol(balanced(4), spot=0.0)
    assert res is None and rejected == {"bad_spot": 1}


def test_rejection_counts_are_returned_even_on_success():
    """A caller has to be able to say WHY a row is thin, not just that it is."""
    quotes = balanced(4) + [quote(strike=100.0, put_call="PUT", iv=-999.0)]
    res, rejected = atm_implied_vol(quotes, spot=SPOT)

    assert res is not None and rejected
