"""The orchestrator, on injected inputs — no ClickHouse, no network."""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from app.services.vol_premium.contract import VolPremiumBuilder
from app.services.vol_premium.iv import ChainQuote
from app.services.vol_premium.schemas import ScreenParams
from app.services.vol_premium.service import VolPremiumService
from app.services.vol_premium.tests.test_iv import balanced


def closes(n=3000, sigma=0.011, seed=3) -> np.ndarray:
    rng = np.random.default_rng(seed)
    w, a, g, b = 2.2e-6, 0.02, 0.13, 0.86
    s2, x, out = sigma**2, 0.0, [100.0]
    for _ in range(n):
        e = math.sqrt(s2) * rng.standard_normal()
        x += e
        out.append(100.0 * math.exp(x))
        s2 = w + (a + (g if e < 0 else 0.0)) * e * e + b * s2
    return np.array(out)


def chain(iv: float, *, age_hours: float = 1.0, n: int = 6) -> list[ChainQuote]:
    ts = datetime.now(timezone.utc) - timedelta(hours=age_hours)
    return balanced(n, iv=iv, ts=ts)


def service(chains: dict[str, list[ChainQuote]], bars=None) -> VolPremiumService:
    bars = bars if bars is not None else closes()
    return VolPremiumService(
        chains_fn=lambda syms: {s: chains.get(s, []) for s in syms},
        closes_fn=lambda s, n: bars,
        universe_fn=lambda: sorted(chains),
    )


def test_satisfies_the_public_contract():
    assert isinstance(service({}), VolPremiumBuilder)


def test_builds_a_row_with_both_halves_and_a_consistent_ratio():
    res = service({"AAA": chain(30.0)}).build_row("AAA")

    assert res.ok and res.row is not None
    row = res.row
    assert row.implied_vol == pytest.approx(30.0)
    assert row.forecast_vol > 0
    assert row.vrp_ratio == pytest.approx(row.implied_vol / row.forecast_vol, rel=1e-3)
    assert row.vrp_points == pytest.approx(row.implied_vol - row.forecast_vol, rel=1e-3)


def test_row_carries_its_forecast_provenance():
    """A ratio must be auditable back to the fit that produced it."""
    row = service({"AAA": chain(30.0)}).build_row("AAA").row

    assert row is not None
    assert 0 < row.garch_persistence < 1
    assert row.garch_sigma_next > 0
    assert row.variance_ratio > 0
    assert row.fit_bars > 500
    assert row.trading_days == 21, "30 calendar days is ~21 trading days"


def test_screen_ranks_most_expensive_first():
    svc = service({"CHEAP": chain(12.0), "MID": chain(28.0), "RICH": chain(70.0)})
    screen = svc.build_screen()

    assert [r.symbol for r in screen.rows] == ["RICH", "MID", "CHEAP"]
    assert screen.rows[0].vrp_ratio > screen.rows[-1].vrp_ratio


def test_identical_forecasts_make_the_ranking_a_pure_iv_ordering():
    """Same underlying series for every symbol, so the ranking must follow IV
    exactly — a sanity check that the denominator is not scrambling the sort."""
    ivs = {f"S{i}": chain(float(iv)) for i, iv in enumerate([45, 15, 30, 60])}
    rows = service(ivs).build_screen().rows

    assert [r.implied_vol for r in rows] == sorted(
        [r.implied_vol for r in rows], reverse=True
    )


def test_universe_comes_from_the_data_when_no_symbols_are_given():
    screen = service({"AAA": chain(20.0), "BBB": chain(25.0)}).build_screen()
    assert {r.symbol for r in screen.rows} == {"AAA", "BBB"}
    assert screen.universe_size == 2


# ── failures are reported, never silently dropped ─────────────────────


def test_missing_chain_is_skipped_with_a_reason():
    screen = service({"AAA": chain(20.0), "GONE": []}).build_screen(["AAA", "GONE"])

    assert [r.symbol for r in screen.rows] == ["AAA"]
    assert len(screen.skipped) == 1
    assert screen.skipped[0].symbol == "GONE"
    assert screen.skipped[0].status == "no_chain"
    assert screen.skipped[0].reason


def test_stale_chain_is_refused():
    """The product's premise is 'today'. A three-day-old chain is not today."""
    svc = service({"OLD": chain(25.0, age_hours=100)})
    res = svc.build_row("OLD", ScreenParams(freshness_hours=36))

    assert res.status == "stale"
    assert "100h old" in (res.reason or "")


def test_unfittable_forecast_is_refused_not_approximated():
    """Without a trustworthy denominator there is no honest ratio."""
    svc = service({"AAA": chain(25.0)}, bars=np.array([100.0, 101.0]))
    res = svc.build_row("AAA")

    assert res.status == "no_forecast"
    assert res.reason


def test_filtered_out_chain_reports_which_gate_rejected_it():
    ts = datetime.now(timezone.utc)
    junk = balanced(6, iv=-999.0, ts=ts)          # all sentinel
    res = service({"AAA": junk}).build_row("AAA")

    assert res.status == "no_chain"
    assert "iv_out_of_range" in (res.reason or "")


def test_a_skipped_symbol_never_looks_like_a_zero_premium_symbol():
    """The failure mode this guards: a symbol quietly missing from a ranked
    table reads as 'nothing interesting here' rather than 'we could not tell'."""
    screen = service({"OK": chain(20.0), "BAD": []}).build_screen(["OK", "BAD"])

    assert "BAD" not in {r.symbol for r in screen.rows}
    assert "BAD" in {s.symbol for s in screen.skipped}
    assert screen.universe_size == 2, "the universe count includes what failed"
