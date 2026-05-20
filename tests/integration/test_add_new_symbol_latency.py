"""
LOCKED LATENCY GATE — when a brand-new symbol is added via
`POST /api/v1/stream`, the cockpit chart must be usable for a 5-year
view within 30 seconds. Failures here are an architectural regression.

Per docs/standards/data/symbol_lifecycle.md the quick-path is:

  silver_ohlcv_build(sym)        # reads bronze.polygon_minute (5y) + corp_actions
  +  schwab_rest_tip_fill(sym)   # fills the 1-2 day gap from yesterday's
                                  # Polygon flat-file → now
  +  silver_to_ch_backfill(sym)  # bulk-insert silver → CH.ohlcv_1m
  +  Schwab WS subscribe         # live ticks forward

End state at T+30s (hard ceiling, 15s expected): CH.ohlcv_1m has
≥10k bars covering the last 30 days, ≥15k bars covering the last
270 days, and ≥1k daily-equivalent bars covering 5 years (via
on-the-fly resample from 1m).

Gate criteria are intentionally generous to absorb network jitter
on the integration runner. The "expected" target (15s typical) is
where we want to live; the 30s ceiling is the regression alarm.

Requires:
  - Live uvicorn at http://localhost:8000 with stream_service started
  - Live ClickHouse (settings.clickhouse_*)
  - Live Schwab credentials (SCHWAB_REFRESH_TOKEN valid)
  - Live AWS for bronze.polygon_minute reads (settings.stock_lake_bucket)

Skipped automatically when any of the above are missing. Run
explicitly via:

  poetry run pytest tests/integration/test_add_new_symbol_latency.py -v

Manual cleanup: the test attempts to remove the symbol from
stream_universe in teardown. If the test process dies mid-run,
operator can clean up via `DELETE /api/v1/stream/{symbol}`.
"""
from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import pytest


pytestmark = pytest.mark.integration


# Locked latency targets (per docs/standards/data/symbol_lifecycle.md).
HARD_CEILING_SECONDS = 30.0
EXPECTED_TARGET_SECONDS = 15.0

# Minimum row-count thresholds for "chart-ready at this zoom level."
# Numbers are deliberately conservative — they assert the layers are
# populated, not that every single bar Polygon has is present.
#
# 30 days × ~390 trading minutes/day × 0.7 (coverage allowance for
# regular-hours only, weekends/holidays/half-days) ≈ 8200 → 10k floor.
MIN_BARS_30D = 10_000
# 270 days × ~78 5-minute bars/day × 0.7 ≈ 14700 → 15k floor.
MIN_BARS_270D_AS_5M_RESAMPLED = 15_000
# 5 years × ~252 trading days × 0.85 ≈ 1070 → 1000 floor for daily.
MIN_DAILY_BARS_5Y = 1_000


# Pool of candidates: established, liquid US equities likely to be
# outside the current stream_universe. Tests pick the first one
# that's actually absent from CH ohlcv_1m on entry — keeps the test
# deterministic without hardcoding a specific name that may have
# drifted into seed.
CANDIDATE_FRESH_SYMBOLS = [
    "AVUV",   # Avantis US Small Cap Value ETF
    "AVDV",   # Avantis Intl Small Cap Value ETF
    "VTV",    # Vanguard Value ETF
    "VUG",    # Vanguard Growth ETF
    "IJR",    # iShares Core S&P Small-Cap ETF
    "IWO",    # iShares Russell 2000 Growth ETF
    "DGRO",   # iShares Core Dividend Growth ETF
    "SCHD",   # Schwab US Dividend Equity ETF
]


def _api_base() -> str:
    return os.getenv("STOCKALERT_TEST_API", "http://localhost:8000").rstrip("/")


def _ch_query(sql: str) -> list[list]:
    """Issue a CH query via the HTTP interface, no client library
    so this test runs without poetry env."""
    from app.config import settings
    import urllib.request
    import urllib.error

    url = (
        f"http://{settings.clickhouse_host}:{settings.clickhouse_port}/"
        f"?database={settings.clickhouse_database}"
    )
    req = urllib.request.Request(
        url,
        data=sql.encode("utf-8"),
        headers={
            "X-ClickHouse-User": settings.clickhouse_user or "default",
            "X-ClickHouse-Key": settings.clickhouse_password or "",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8").strip()
    if not body:
        return []
    return [line.split("\t") for line in body.split("\n")]


def _ch_count(symbol: str, *, since: datetime) -> int:
    """Count CH rows for `symbol` with `timestamp >= since`."""
    sql = (
        f"SELECT count() FROM ohlcv_1m "
        f"WHERE symbol = '{symbol}' AND timestamp >= toDateTime64({since.timestamp()}, 3, 'UTC')"
    )
    rows = _ch_query(sql)
    if not rows or not rows[0]:
        return 0
    return int(rows[0][0])


def _ch_daily_resampled_count(symbol: str, *, since: datetime) -> int:
    """Count distinct trading days in ohlcv_1m for `symbol` since
    `since` — equivalent to the rows a 5y daily chart would render."""
    sql = (
        f"SELECT count(DISTINCT toStartOfDay(timestamp)) FROM ohlcv_1m "
        f"WHERE symbol = '{symbol}' AND timestamp >= toDateTime64({since.timestamp()}, 3, 'UTC')"
    )
    rows = _ch_query(sql)
    if not rows or not rows[0]:
        return 0
    return int(rows[0][0])


def _ch_total_count(symbol: str) -> int:
    sql = f"SELECT count() FROM ohlcv_1m WHERE symbol = '{symbol}'"
    rows = _ch_query(sql)
    return int(rows[0][0]) if rows and rows[0] else 0


def _post_stream_add(symbol: str) -> tuple[int, dict]:
    """POST /api/v1/stream — returns (status_code, body)."""
    import json
    import urllib.request
    import urllib.error

    req = urllib.request.Request(
        f"{_api_base()}/api/v1/stream",
        data=json.dumps({"symbol": symbol, "notes": "latency gate test"}).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, _safe_json(resp.read())
    except urllib.error.HTTPError as e:
        return e.code, _safe_json(e.read())


def _delete_stream(symbol: str) -> None:
    """DELETE /api/v1/stream/{symbol} — best-effort teardown."""
    import urllib.request
    import urllib.error

    req = urllib.request.Request(
        f"{_api_base()}/api/v1/stream/{symbol}",
        method="DELETE",
    )
    try:
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:  # noqa: BLE001
        pass


def _safe_json(b: bytes) -> dict:
    import json
    try:
        return json.loads(b.decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _pick_fresh_symbol() -> str:
    """Pick the first candidate that has 0 rows in CH.ohlcv_1m."""
    for sym in CANDIDATE_FRESH_SYMBOLS:
        if _ch_total_count(sym) == 0:
            return sym
    pytest.skip(
        "All CANDIDATE_FRESH_SYMBOLS already have CH data — pick a new "
        "candidate that the operator hasn't streamed yet."
    )


def _check_api_alive() -> None:
    """Skip the test cleanly when the API server isn't running."""
    import urllib.request
    import urllib.error

    try:
        urllib.request.urlopen(f"{_api_base()}/health", timeout=2).read()
    except (urllib.error.URLError, TimeoutError) as e:
        pytest.skip(f"API not reachable at {_api_base()}: {e}")


# ─────────────────────────────────────────────────────────────────────
# The locked gate test
# ─────────────────────────────────────────────────────────────────────


def test_add_new_symbol_populates_5y_chart_within_30s():
    """LOCKED LATENCY GATE.

    Pick a brand-new symbol absent from stream_universe + ohlcv_1m, POST
    it via /api/v1/stream, poll CH until all three zoom levels have
    chart-ready data covering their windows. Assert total wall-clock
    ≤ HARD_CEILING_SECONDS.

    Violations of this gate are architectural regressions — the
    docs/standards/data/symbol_lifecycle.md "Quick path" promise is
    that any zoom level of the chart is populated within 30s of an
    operator click. If this test fails, the warmup chain has
    regressed (or the underlying bronze/silver/CH layers have).
    """
    _check_api_alive()
    symbol = _pick_fresh_symbol()

    now = datetime.now(timezone.utc)
    win_30d = now - timedelta(days=30)
    win_270d = now - timedelta(days=270)
    win_5y = now - timedelta(days=5 * 365)

    t0 = time.monotonic()

    status, body = _post_stream_add(symbol)
    assert status == 201, f"add failed: {status} {body}"
    assert symbol in body.get("changed", []), f"unexpected body: {body}"

    last_obs = {"c30d": 0, "c270d": 0, "cdaily5y": 0}

    deadline = t0 + HARD_CEILING_SECONDS
    while time.monotonic() < deadline:
        c30d = _ch_count(symbol, since=win_30d)
        c270d = _ch_count(symbol, since=win_270d)
        cdaily5y = _ch_daily_resampled_count(symbol, since=win_5y)

        last_obs = {"c30d": c30d, "c270d": c270d, "cdaily5y": cdaily5y}

        if (
            c30d >= MIN_BARS_30D
            and c270d >= MIN_BARS_270D_AS_5M_RESAMPLED
            and cdaily5y >= MIN_DAILY_BARS_5Y
        ):
            elapsed = time.monotonic() - t0
            print(
                f"\n✓ LATENCY GATE PASSED at {elapsed:.1f}s — "
                f"30d={c30d} 270d={c270d} daily_5y={cdaily5y}"
            )
            if elapsed > EXPECTED_TARGET_SECONDS:
                print(
                    f"  ⚠ above expected {EXPECTED_TARGET_SECONDS}s target "
                    f"but within {HARD_CEILING_SECONDS}s ceiling"
                )
            # Teardown
            _delete_stream(symbol)
            return

        time.sleep(0.5)

    elapsed = time.monotonic() - t0
    _delete_stream(symbol)
    pytest.fail(
        f"LATENCY GATE FAILED at {elapsed:.1f}s "
        f"(ceiling {HARD_CEILING_SECONDS}s) — last observation: "
        f"ohlcv_1m 30d={last_obs['c30d']} (need ≥{MIN_BARS_30D}), "
        f"ohlcv_1m 270d={last_obs['c270d']} (need ≥{MIN_BARS_270D_AS_5M_RESAMPLED}), "
        f"daily_5y={last_obs['cdaily5y']} (need ≥{MIN_DAILY_BARS_5Y}). "
        f"Quick-path warmup chain or bronze→silver→CH pipeline is broken."
    )
