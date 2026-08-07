"""
Freshness classification tests.

These pin the FAILURE modes, not the happy path — the whole point of the
panel is to be trustworthy on the bad day. The last two tests replay the
real 2026-07/08 incidents: if this layer had existed, they'd have been
visible in one glance instead of two weeks later.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.services.health.freshness import (
    ExpectedWindow,
    FreshnessSource,
    classify,
    is_expected_fresh,
)

# 2026-08-06 was a Thursday. 15:00 UTC = 11:00 ET (mid-session);
# 07:00 UTC = 03:00 ET (overnight).
MIDDAY = datetime(2026, 8, 6, 15, 0, tzinfo=timezone.utc)
OVERNIGHT = datetime(2026, 8, 6, 7, 0, tzinfo=timezone.utc)
SATURDAY = datetime(2026, 8, 8, 15, 0, tzinfo=timezone.utc)


def _src(**kw) -> FreshnessSource:
    base = dict(
        key="t", label="Test", group="Market data", cadence_seconds=60,
        window=ExpectedWindow.MARKET_HOURS, probe=lambda: None,
    )
    base.update(kw)
    return FreshnessSource(**base)


# ── the expected-fresh window (the anti-false-alarm rule) ────────────


def test_intraday_source_is_expected_fresh_only_during_the_session():
    assert is_expected_fresh(ExpectedWindow.MARKET_HOURS, now=MIDDAY)
    assert not is_expected_fresh(ExpectedWindow.MARKET_HOURS, now=OVERNIGHT)
    assert not is_expected_fresh(ExpectedWindow.MARKET_HOURS, now=SATURDAY)


def test_always_window_ignores_the_session():
    assert is_expected_fresh(ExpectedWindow.ALWAYS, now=OVERNIGHT)
    assert is_expected_fresh(ExpectedWindow.ALWAYS, now=SATURDAY)


# ── classification ───────────────────────────────────────────────────


def test_fresh_is_ok():
    row = classify(_src(), MIDDAY - timedelta(seconds=30), now=MIDDAY)
    assert row.state == "ok" and row.expected_fresh


def test_two_cadences_late_warns_ten_errors():
    warn = classify(_src(), MIDDAY - timedelta(seconds=150), now=MIDDAY)
    err = classify(_src(), MIDDAY - timedelta(seconds=900), now=MIDDAY)
    assert warn.state == "warn"
    assert err.state == "error"


def test_same_staleness_overnight_is_idle_not_error():
    """THE rule that keeps this panel trustworthy: a 1-min feed 14h stale
    at 03:00 ET is normal. Flagging it red nightly would train the user
    to ignore the panel, which is worse than not having one."""
    stale = OVERNIGHT - timedelta(hours=14)
    row = classify(_src(), stale, now=OVERNIGHT)
    assert row.state == "idle" and not row.expected_fresh
    # ...and the very same age mid-session IS an error.
    assert classify(_src(), MIDDAY - timedelta(hours=14), now=MIDDAY).state == "error"


def test_no_data_is_error_in_window_unknown_outside():
    assert classify(_src(), None, now=MIDDAY).state == "error"
    assert classify(_src(), None, now=OVERNIGHT).state == "unknown"


def test_naive_timestamps_are_treated_as_utc():
    """ClickHouse hands back naive datetimes; the platform convention is
    that naive == UTC. Mis-reading them shifts age by the local offset."""
    naive = (MIDDAY - timedelta(seconds=30)).replace(tzinfo=None)
    row = classify(_src(), naive, now=MIDDAY)
    assert row.state == "ok" and 0 <= (row.age_seconds or 0) < 60


def test_nightly_batch_is_idle_before_the_close_and_graded_after():
    src = _src(cadence_seconds=86_400, window=ExpectedWindow.AFTER_CLOSE)
    yesterday = MIDDAY - timedelta(days=1)
    # 11:00 ET — tonight's batch hasn't run yet; yesterday's data is right.
    assert classify(src, yesterday, now=MIDDAY).state == "idle"
    # 19:00 ET (23:00 UTC) — it should have landed by now.
    evening = datetime(2026, 8, 6, 23, 0, tzinfo=timezone.utc)
    assert classify(src, yesterday, now=evening).state == "ok"


# ── incident replays ─────────────────────────────────────────────────


def test_replays_the_dead_streamer_incident():
    """2026-07-20 → 08-05: the Schwab streamer thread died while
    /stream/status still reported started=true, provider_ready=true,
    streaming_count=240. ClickHouse took zero bars for 15 days and
    nothing surfaced it."""
    src = _src(key="live_bars", cadence_seconds=180)
    row = classify(src, MIDDAY - timedelta(days=15), now=MIDDAY)
    assert row.state == "error"
    assert "15d old" in row.detail


def test_replays_the_stale_options_bronze_incident():
    """The ThetaData bronze sat 34 days stale after the terminal stopped."""
    src = _src(key="theta_bronze", cadence_seconds=86_400,
               window=ExpectedWindow.AFTER_CLOSE)
    evening = datetime(2026, 8, 6, 23, 0, tzinfo=timezone.utc)
    row = classify(src, evening - timedelta(days=34), now=evening)
    assert row.state == "error"


@pytest.mark.parametrize("window", [ExpectedWindow.MARKET_HOURS, ExpectedWindow.AFTER_CLOSE])
def test_weekend_never_alarms(window):
    row = classify(_src(window=window), SATURDAY - timedelta(days=3), now=SATURDAY)
    assert row.state == "idle"


# ── endpoint isolation ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_one_failing_probe_does_not_break_the_response(monkeypatch):
    """A bad probe becomes an `unknown` ROW, never a 5xx — the panel has
    to stay readable precisely when something is broken."""
    import app.api.routes_health as rh
    from app.services.health import freshness as F

    def _boom():
        raise RuntimeError("ClickHouse unreachable")

    monkeypatch.setattr(F, "SOURCES", [
        # the endpoint classifies against real "now", so use it here
        _src(key="good", label="Good", window=ExpectedWindow.ALWAYS,
             probe=lambda: datetime.now(timezone.utc)),
        _src(key="bad", label="Bad", probe=_boom),
    ])
    resp = await rh.health_freshness()
    by_key = {r.key: r for r in resp.rows}
    assert by_key["bad"].state == "unknown"
    assert "RuntimeError" in by_key["bad"].detail
    assert by_key["good"].state == "ok"


@pytest.mark.asyncio
async def test_a_hanging_probe_cannot_hold_the_response(monkeypatch):
    """2026-08-06: blocking work inside a coroutine froze every endpoint
    for 40+ minutes. This surface must fail fast instead of waiting."""
    import asyncio
    import time

    import app.api.routes_health as rh
    from app.services.health import freshness as F

    monkeypatch.setattr(rh, "_FRESHNESS_PROBE_TIMEOUT_S", 0.2)
    monkeypatch.setattr(F, "SOURCES", [
        _src(key="slow", label="Slow", probe=lambda: time.sleep(10)),
    ])
    started = asyncio.get_running_loop().time()
    resp = await rh.health_freshness()
    elapsed = asyncio.get_running_loop().time() - started

    assert elapsed < 3.0, f"endpoint waited {elapsed:.1f}s on a hanging probe"
    assert resp.rows[0].state == "unknown"
    assert "timed out" in resp.rows[0].detail


@pytest.mark.asyncio
async def test_rows_are_sorted_worst_first(monkeypatch):
    import app.api.routes_health as rh
    from app.services.health import freshness as F

    monkeypatch.setattr(F, "SOURCES", [
        _src(key="fine", label="Fine", probe=lambda: datetime.now(timezone.utc)),
        _src(key="broken", label="Broken", window=ExpectedWindow.ALWAYS,
             probe=lambda: datetime.now(timezone.utc) - timedelta(days=30)),
    ])
    resp = await rh.health_freshness()
    assert resp.rows[0].key == "broken"
    assert resp.worst_state == "error"
