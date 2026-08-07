"""
Data-source FRESHNESS — "is data still arriving?"

Deliberately separate from `/health/services`, which answers "can I
connect?". Every incident on 2026-08-05/06 answered that question with
YES while data had silently stopped:

  * the Schwab streamer thread died 2026-07-20; `/stream/status` still
    reported started=true, provider_ready=true, streaming_count=240 —
    and ClickHouse took zero new bars for 15 days.
  * the ThetaData options bronze sat 34 days stale after the terminal
    stopped; nothing surfaced it.
  * 552 instrument names were cached blank; the pages just showed bare
    tickers.

Connectivity was green throughout. Freshness is the missing signal.

**The load-bearing design rule is the expected-fresh window.** A
1-minute bar feed that is 14 hours stale at 03:00 is IDLE, not broken;
the same reading at 11:00 ET is an ERROR. A panel that cries wolf every
night gets ignored within a week, which is worse than no panel — so
every source declares when it is *expected* to be fresh, and staleness
outside that window is reported as `idle`.

Adding a source is one `FreshnessSource` entry; no other code changes.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Callable, Literal, Optional
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)

ET = ZoneInfo("America/New_York")

FreshnessState = Literal["ok", "warn", "error", "idle", "unknown"]

# Staleness is measured in multiples of the source's own cadence, so one
# rule fits a 1-minute feed and a daily file alike.
WARN_CADENCES = 2.0
ERROR_CADENCES = 10.0

# Regular US equities session (ET). Intraday feeds are only expected to
# be fresh inside it.
RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)


class ExpectedWindow:
    """When a source is expected to be producing data."""

    ALWAYS = "always"                # e.g. a token's age, name coverage
    MARKET_HOURS = "market_hours"    # intraday feeds
    AFTER_CLOSE = "after_close"      # nightly/EOD batches


@dataclass(frozen=True)
class FreshnessSource:
    """One monitored data source.

    `probe` returns the source's newest data timestamp (UTC), or None if
    the source has no data at all. It MUST be cheap — a `max(...)` — and
    it may raise: a raising probe becomes `unknown`, never an outage.
    """
    key: str
    label: str
    group: str                       # "Market data" | "Options" | "Platform"
    cadence_seconds: float
    window: str
    probe: Callable[[], Optional[datetime]]
    detail_hint: str = ""


@dataclass
class FreshnessRow:
    """Result for one source — the API/UI shape."""
    key: str
    label: str
    group: str
    state: FreshnessState
    last_data_at: Optional[str]      # ISO-8601 UTC
    age_seconds: Optional[float]
    cadence_seconds: float
    expected_fresh: bool
    detail: str


# ── window logic ─────────────────────────────────────────────────────


def _is_equities_session(d: date) -> bool:
    """Trading day per the platform calendar; falls back to weekday-only
    if the calendar package is unavailable (never fail a health probe)."""
    try:
        from app.services.market_calendar import is_equities_session

        return bool(is_equities_session(d))
    except Exception as exc:  # noqa: BLE001 — degrade, don't break
        logger.debug("freshness: market calendar unavailable (%s)", exc)
        return d.weekday() < 5


def is_expected_fresh(window: str, *, now: Optional[datetime] = None) -> bool:
    """Should this source be producing data right now?"""
    if window == ExpectedWindow.ALWAYS:
        return True
    now_et = (now or datetime.now(timezone.utc)).astimezone(ET)
    if not _is_equities_session(now_et.date()):
        return False
    if window == ExpectedWindow.MARKET_HOURS:
        return RTH_OPEN <= now_et.time() <= RTH_CLOSE
    if window == ExpectedWindow.AFTER_CLOSE:
        # Nightly batches land after the close; before that, "yesterday's
        # data" is the correct state, not a stale one.
        return now_et.time() >= time(18, 0)
    return True


def classify(
    source: FreshnessSource,
    last_data_at: Optional[datetime],
    *,
    now: Optional[datetime] = None,
) -> FreshnessRow:
    """Turn a probe result into a state, honouring the expected window."""
    now = now or datetime.now(timezone.utc)
    expected = is_expected_fresh(source.window, now=now)

    if last_data_at is None:
        return FreshnessRow(
            key=source.key, label=source.label, group=source.group,
            state="error" if expected else "unknown",
            last_data_at=None, age_seconds=None,
            cadence_seconds=source.cadence_seconds,
            expected_fresh=expected,
            detail="no data found for this source",
        )

    if last_data_at.tzinfo is None:  # naive == UTC by platform convention
        last_data_at = last_data_at.replace(tzinfo=timezone.utc)
    age = (now - last_data_at).total_seconds()
    cadences = age / source.cadence_seconds if source.cadence_seconds else 0.0

    if not expected:
        # Outside the window staleness is EXPECTED. Reporting it as an
        # error is how a panel like this gets ignored.
        state: FreshnessState = "idle"
        detail = f"{_ago(age)} old — outside expected window (normal)"
    elif cadences >= ERROR_CADENCES:
        state = "error"
        detail = f"{_ago(age)} old — expected every {_ago(source.cadence_seconds)}"
    elif cadences >= WARN_CADENCES:
        state = "warn"
        detail = f"{_ago(age)} old — expected every {_ago(source.cadence_seconds)}"
    else:
        state = "ok"
        detail = f"{_ago(age)} old"
    if source.detail_hint and state in ("warn", "error"):
        detail = f"{detail} · {source.detail_hint}"

    return FreshnessRow(
        key=source.key, label=source.label, group=source.group, state=state,
        last_data_at=last_data_at.astimezone(timezone.utc).isoformat(),
        age_seconds=age, cadence_seconds=source.cadence_seconds,
        expected_fresh=expected, detail=detail,
    )


def _ago(seconds: float) -> str:
    seconds = max(float(seconds), 0.0)
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds / 60:.0f}m"
    if seconds < 172800:
        return f"{seconds / 3600:.0f}h"
    return f"{seconds / 86400:.0f}d"


# ── probes (each cheap, each allowed to raise) ───────────────────────


def _ch_max(query: str) -> Optional[datetime]:
    from app.db.client import get_client

    rows = get_client().query(query).result_rows
    if not rows or rows[0][0] is None:
        return None
    val = rows[0][0]
    if isinstance(val, date) and not isinstance(val, datetime):
        val = datetime.combine(val, time(0, 0))
    return val if isinstance(val, datetime) else None


def _probe_live_bars() -> Optional[datetime]:
    return _ch_max("SELECT max(timestamp) FROM ohlcv_1m")


def _probe_daily_bars() -> Optional[datetime]:
    return _ch_max("SELECT max(timestamp) FROM ohlcv_daily")


def _probe_options_gex() -> Optional[datetime]:
    return _ch_max("SELECT max(snapshot_ts) FROM options_gex_latest")


def _iceberg_last_commit(table_id: str) -> Optional[datetime]:
    """When data last LANDED in an Iceberg table, from the current
    snapshot's commit time.

    Deliberately not a data scan or partition sweep: these tables
    partition on transforms (month ordinals, buckets), so partition
    values aren't timestamps at all, and inspecting them measured
    22-71s — far too slow for a health probe. The snapshot timestamp
    is O(1) metadata and is exactly the question being asked.
    """
    from app.services.iceberg_catalog import get_catalog

    snap = get_catalog().load_table(table_id).current_snapshot()
    if snap is None:
        return None
    return datetime.fromtimestamp(snap.timestamp_ms / 1000, tz=timezone.utc)


def _probe_equities_lake() -> Optional[datetime]:
    from app.services.equities.schemas import equities_table_id

    return _iceberg_last_commit(equities_table_id("schwab_universe"))


def _probe_theta_bronze() -> Optional[datetime]:
    from app.services.options.tables import options_table_id

    return _iceberg_last_commit(options_table_id("thetadata_greeks_eod"))


def _probe_paper_runs() -> Optional[datetime]:
    return _ch_max("SELECT max(last_run_at) FROM paper_state")


def _probe_instrument_names() -> Optional[datetime]:
    """When company names were last refreshed. Framed as a timestamp so
    every row in this panel shares ONE contract (see the 552 blank names
    incident — the failure was staleness, and this makes it visible)."""
    return _ch_max("SELECT max(updated_at) FROM instrument_names")


SOURCES: list[FreshnessSource] = [
    FreshnessSource(
        key="live_bars", label="Live bars (Schwab stream)", group="Market data",
        # Bars are 1-minute, but a bar for minute T only lands after T
        # closes and the batcher flushes, so real steady-state lag is
        # 1-3 min. Calling that "late" would keep this row permanently
        # amber and train you to ignore it. 3 min => warn at 6, error at
        # 30 — still catches a dead streamer within the hour.
        cadence_seconds=180, window=ExpectedWindow.MARKET_HOURS,
        probe=_probe_live_bars,
        detail_hint="check the streamer thread — it can die while /stream/status still reads healthy",
    ),
    FreshnessSource(
        key="daily_bars", label="Daily bars (Polygon)", group="Market data",
        cadence_seconds=86_400, window=ExpectedWindow.AFTER_CLOSE,
        probe=_probe_daily_bars,
    ),
    FreshnessSource(
        key="equities_lake", label="Equities lake (schwab_universe)", group="Market data",
        cadence_seconds=300, window=ExpectedWindow.MARKET_HOURS,
        probe=_probe_equities_lake,
        detail_hint="live_lake_writer may be disabled",
    ),
    FreshnessSource(
        key="options_gex", label="Options chains → GEX (Schwab)", group="Options",
        cadence_seconds=900, window=ExpectedWindow.MARKET_HOURS,
        probe=_probe_options_gex,
    ),
    FreshnessSource(
        key="theta_bronze", label="Options bronze (ThetaData EOD)", group="Options",
        cadence_seconds=86_400, window=ExpectedWindow.AFTER_CLOSE,
        probe=_probe_theta_bronze,
        detail_hint="Theta Terminal must be running; history excludes the current day",
    ),
    FreshnessSource(
        key="paper_runs", label="Paper strategy runs", group="Platform",
        cadence_seconds=86_400, window=ExpectedWindow.ALWAYS,
        probe=_probe_paper_runs,
    ),
    FreshnessSource(
        key="instrument_names", label="Company names", group="Platform",
        cadence_seconds=7 * 86_400, window=ExpectedWindow.ALWAYS,
        probe=_probe_instrument_names,
        detail_hint="re-run bulk_refresh_from_reference()",
    ),
]
