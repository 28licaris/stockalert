"""The historical baseline reducer.

The load-bearing test here is `test_agrees_with_the_live_estimator`: today's
reading comes from `iv.py` and the percentile it is judged against comes from
this module. If the two measure differently, the percentile is meaningless —
so they are asserted to agree on identical input.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import polars as pl
import pytest

from app.services.vol_premium.baseline import (
    percentile_of,
    reduce_eod_chains,
)
from app.services.vol_premium.iv import ChainQuote, atm_implied_vol
from app.services.vol_premium.schemas import ScreenParams

EOD = date(2026, 7, 1)


def row(*, symbol="AAA", strike=100.0, put_call="CALL", iv=0.25,
        exp=date(2026, 7, 31), spot=100.0, eod=EOD) -> dict:
    """One raw ThetaData EOD contract row. `iv` is a DECIMAL, as they report it."""
    return {
        "underlying_symbol": symbol, "eod_date": eod, "expiration_date": exp,
        "strike": strike, "put_call": put_call, "implied_vol": iv,
        "underlying_price": spot,
    }


def frame(rows: list[dict]) -> pl.DataFrame:
    return pl.DataFrame(rows)


def balanced(n=4, **kw) -> list[dict]:
    return [
        row(strike=99.0 + i * 0.5, put_call="CALL" if i % 2 == 0 else "PUT", **kw)
        for i in range(n)
    ]


# ── units ─────────────────────────────────────────────────────────────


def test_converts_thetadata_decimals_to_percent():
    """ThetaData sends 0.25; the live path speaks 25.0. A missed conversion
    would misprice every comparison by 100x."""
    out = reduce_eod_chains(frame(balanced(4, iv=0.25)))

    assert out.height == 1
    assert out["atm_iv"][0] == pytest.approx(25.0)


def test_percent_input_is_left_alone_when_declared():
    out = reduce_eod_chains(frame(balanced(4, iv=25.0)), iv_is_decimal=False)
    assert out["atm_iv"][0] == pytest.approx(25.0)


# ── the gates, matching iv.py ─────────────────────────────────────────


def test_drops_out_of_range_iv():
    """Covers provider sentinels and solver blowups in one filter."""
    rows = balanced(4, iv=0.25) + [row(strike=100.0, put_call="PUT", iv=-9.99),
                                   row(strike=100.5, put_call="CALL", iv=8.5)]
    out = reduce_eod_chains(frame(rows))

    assert out["atm_iv"][0] == pytest.approx(25.0)
    assert out["contracts"][0] == 4


def test_refuses_a_one_sided_expiry():
    rows = [row(strike=99.0 + i * 0.4, put_call="CALL") for i in range(6)]
    assert reduce_eod_chains(frame(rows)).is_empty()


def test_drops_strikes_outside_the_band():
    rows = balanced(4) + [row(strike=140.0, put_call="CALL"),
                          row(strike=60.0, put_call="PUT")]
    out = reduce_eod_chains(frame(rows))
    assert out["contracts"][0] == 4


def test_requires_minimum_support():
    rows = [row(strike=100.0, put_call="CALL"), row(strike=100.0, put_call="PUT")]
    assert reduce_eod_chains(frame(rows), params=ScreenParams(min_contracts=4)).is_empty()


def test_picks_the_expiry_nearest_the_target_and_breaks_ties_long():
    near = balanced(4, exp=date(2026, 7, 26), iv=0.20)   # 25 dte
    far = balanced(4, exp=date(2026, 8, 5), iv=0.40)     # 35 dte
    out = reduce_eod_chains(frame(near + far), params=ScreenParams(target_dte=30))

    assert out.height == 1
    assert out["days_to_expiration"][0] == 35, "ties break toward the longer expiry"
    assert out["atm_iv"][0] == pytest.approx(40.0)


def test_ignores_expiries_outside_the_window():
    rows = balanced(4, exp=date(2026, 7, 3)) + balanced(4, exp=date(2027, 6, 1))
    assert reduce_eod_chains(frame(rows)).is_empty()


# ── shape ─────────────────────────────────────────────────────────────


def test_one_row_per_symbol_per_day():
    rows = []
    for sym in ("AAA", "BBB"):
        for d in (date(2026, 7, 1), date(2026, 7, 2)):
            rows += balanced(4, symbol=sym, eod=d,
                             exp=date(2026, 7, 31), iv=0.3)
    out = reduce_eod_chains(frame(rows))

    assert out.height == 4
    assert out.select(["symbol", "eod_date"]).n_unique() == 4
    assert out["symbol"].to_list() == ["AAA", "AAA", "BBB", "BBB"]


def test_counts_calls_and_puts_separately():
    rows = [row(strike=99.5, put_call="CALL"), row(strike=100.0, put_call="CALL"),
            row(strike=100.5, put_call="PUT"), row(strike=101.0, put_call="PUT"),
            row(strike=99.0, put_call="PUT")]
    out = reduce_eod_chains(frame(rows))

    assert (out["calls"][0], out["puts"][0], out["contracts"][0]) == (2, 3, 5)


def test_empty_input_returns_empty_not_an_error():
    assert reduce_eod_chains(pl.DataFrame()).is_empty()


# ── the agreement that makes the percentile meaningful ────────────────


def test_agrees_with_the_live_estimator():
    """The whole point of the baseline.

    Today's number comes from `iv.py` (Schwab, percent). The history it is
    ranked against comes from this module (ThetaData, decimal). Same chain in,
    same at-the-money volatility out — otherwise "96th percentile" compares two
    different measurements and means nothing.
    """
    spec = [
        (99.0, "CALL", 0.240), (99.5, "PUT", 0.260),
        (100.0, "CALL", 0.250), (100.5, "PUT", 0.255),
        (101.0, "CALL", 0.245), (140.0, "CALL", 0.900),   # outside the band
        (100.0, "PUT", -9.99),                            # sentinel
    ]
    exp = date(2026, 7, 31)

    hist = reduce_eod_chains(frame([
        row(strike=k, put_call=pc, iv=iv, exp=exp) for k, pc, iv in spec
    ]))

    live, _ = atm_implied_vol(
        [
            ChainQuote(
                expiration_date=exp, days_to_expiration=(exp - EOD).days,
                strike=k, put_call=pc,
                implied_vol=iv * 100.0,          # Schwab speaks percent
                bid=4.0, ask=4.2, mark=4.1, open_interest=500,
                underlying_price=100.0,
                snapshot_ts=datetime(2026, 7, 1, 20, tzinfo=timezone.utc),
            )
            for k, pc, iv in spec
        ],
        spot=100.0,
    )

    assert live is not None and hist.height == 1
    assert hist["atm_iv"][0] == pytest.approx(live.implied_vol, rel=1e-9)
    assert hist["contracts"][0] == live.contracts_used
    assert (hist["calls"][0], hist["puts"][0]) == (live.calls, live.puts)


# ── the percentile ────────────────────────────────────────────────────


def test_percentile_places_a_value_in_its_history():
    hist = list(range(100))          # 0..99
    assert percentile_of(50, hist) == pytest.approx(50.0)
    assert percentile_of(-1, hist) == pytest.approx(0.0)
    assert percentile_of(1000, hist) == pytest.approx(100.0)


def test_percentile_of_empty_history_is_none_not_a_default():
    """"No baseline" and "unremarkable" are opposite claims. A default of 50
    would quietly present the first as the second."""
    assert percentile_of(1.2, []) is None
    assert percentile_of(1.2, [None, None]) is None
