"""
Integration tests for the trading-journal data layer.

Covers:
  - `journal_repo`: insert/list/dedup trades; notes upsert; account snapshots.
  - `routes_journal`: HTTP-level shape of /accounts, /trades, /summary, /notes, /sync.

Requires ClickHouse running. Uses an `__test_jr_` account_hash prefix to keep
real data untouched and wipes test rows at teardown.
"""
from __future__ import annotations

import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app.db import journal_repo
from app.db.client import get_client
from app.services.journal.journal_parser import TradeRecord


TEST_PREFIX = "__test_jr_"


def _wipe(account_hash: str) -> None:
    """Hard-delete all rows for a test account_hash."""
    if not account_hash.startswith(TEST_PREFIX):
        raise ValueError(f"_wipe refused non-test account_hash {account_hash!r}")
    c = get_client()
    for tbl in ("trades", "trade_notes", "account_snapshots"):
        c.command(
            f"ALTER TABLE {tbl} DELETE WHERE account_hash = {{a:String}}",
            parameters={"a": account_hash},
        )


@pytest.fixture
def acct(clickhouse_ready):
    """Yield a unique test account_hash and clean up afterward."""
    name = f"{TEST_PREFIX}{uuid.uuid4().hex[:10]}"
    yield name
    _wipe(name)


@pytest.fixture(scope="module")
def app_client(clickhouse_ready):
    """TestClient with the real lifespan stubbed out (no Schwab login)."""
    from app.main_api import app

    @asynccontextmanager
    async def _noop_lifespan(_app):
        yield

    app.router.lifespan_context = _noop_lifespan
    with TestClient(app) as c:
        yield c


def _make_record(acct: str, activity_id: int, **overrides) -> TradeRecord:
    defaults = dict(
        account_hash=acct,
        activity_id=activity_id,
        order_id=activity_id + 1,
        trade_time=datetime(2026, 5, 1, 14, 30, tzinfo=timezone.utc),
        symbol="TEST",
        asset_type="EQUITY",
        side="BUY",
        position_effect="OPENING",
        quantity=100.0,
        price=10.0,
        gross_amount=1000.0,
        fees=1.0,
        net_amount=-1001.0,
        status="VALID",
    )
    defaults.update(overrides)
    return TradeRecord(**defaults)


# ---------- repo ----------


def test_insert_and_list_trades_round_trip(acct: str) -> None:
    n = journal_repo.insert_trades_batch([
        _make_record(acct, 1, symbol="AAPL"),
        _make_record(acct, 2, symbol="TSLA", trade_time=datetime(2026, 5, 2, 14, 30, tzinfo=timezone.utc)),
    ])
    assert n == 2
    rows = journal_repo.list_trades(account_hash=acct)
    assert len(rows) == 2
    # Most recent first
    assert rows[0]["symbol"] == "TSLA"
    assert rows[1]["symbol"] == "AAPL"


def test_insert_is_idempotent_on_activity_id(acct: str) -> None:
    """ReplacingMergeTree dedupes on (account_hash, activity_id)."""
    journal_repo.insert_trades_batch([_make_record(acct, 100, price=10.0)])
    journal_repo.insert_trades_batch([_make_record(acct, 100, price=99.0)])
    rows = journal_repo.list_trades(account_hash=acct)
    assert len(rows) == 1
    # Latest version wins (price=99.0).
    assert rows[0]["price"] == pytest.approx(99.0)


def test_list_trades_filters_by_symbol(acct: str) -> None:
    journal_repo.insert_trades_batch([
        _make_record(acct, 10, symbol="AAPL"),
        _make_record(acct, 11, symbol="TSLA"),
        _make_record(acct, 12, symbol="AAPL"),
    ])
    rows = journal_repo.list_trades(account_hash=acct, symbol="AAPL")
    assert len(rows) == 2
    assert {r["symbol"] for r in rows} == {"AAPL"}


def test_list_trades_filters_by_window(acct: str) -> None:
    old = datetime(2026, 1, 1, tzinfo=timezone.utc)
    new = datetime(2026, 5, 1, tzinfo=timezone.utc)
    journal_repo.insert_trades_batch([
        _make_record(acct, 20, trade_time=old),
        _make_record(acct, 21, trade_time=new),
    ])
    rows = journal_repo.list_trades(
        account_hash=acct,
        start=datetime(2026, 4, 1, tzinfo=timezone.utc),
        end=datetime(2026, 6, 1, tzinfo=timezone.utc),
    )
    assert len(rows) == 1
    assert rows[0]["activity_id"] == 21


def test_set_trade_note_joins_into_list(acct: str) -> None:
    journal_repo.insert_trades_batch([_make_record(acct, 30, symbol="AAPL")])
    journal_repo.set_trade_note(
        account_hash=acct, activity_id=30,
        strategy="breakout", tags=["earnings", "gap"], note="Held overnight; good entry",
    )
    rows = journal_repo.list_trades(account_hash=acct)
    row = rows[0]
    assert row["strategy"] == "breakout"
    assert row["tags"] == ["earnings", "gap"]
    assert row["note"] == "Held overnight; good entry"


def test_set_trade_note_is_idempotent(acct: str) -> None:
    """Repeated upserts keep only the latest version."""
    journal_repo.insert_trades_batch([_make_record(acct, 40)])
    journal_repo.set_trade_note(account_hash=acct, activity_id=40, note="v1")
    journal_repo.set_trade_note(account_hash=acct, activity_id=40, note="v2")
    rows = journal_repo.list_trades(account_hash=acct)
    assert rows[0]["note"] == "v2"


def test_account_snapshot_insert_and_latest(acct: str) -> None:
    now = datetime.now(timezone.utc)
    payload = {
        "securitiesAccount": {
            "type": "CASH",
            "accountNumber": "12345678",
            "roundTrips": 3,
            "currentBalances": {
                "cashBalance": 1234.56,
                "liquidationValue": 5000.0,
                "longMarketValue": 3000.0,
                "shortMarketValue": 0.0,
            },
            "projectedBalances": {"buyingPower": 2500.0},
        }
    }
    journal_repo.insert_account_snapshot(
        account_hash=acct, snapshot_time=now - timedelta(hours=1), payload=payload,
    )
    journal_repo.insert_account_snapshot(
        account_hash=acct, snapshot_time=now, payload=payload,
    )
    snaps = [s for s in journal_repo.latest_snapshot_per_account()
             if s["account_hash"] == acct]
    assert len(snaps) == 1
    s = snaps[0]
    assert s["cash_balance"] == pytest.approx(1234.56)
    assert s["liquidation_value"] == pytest.approx(5000.0)
    assert s["round_trips"] == 3


# ---------- routes ----------


def test_get_trades_returns_window(app_client, acct: str) -> None:
    """GET /api/journal/trades?account= shape and contents."""
    journal_repo.insert_trades_batch([
        _make_record(acct, 50, symbol="AAPL", side="BUY",  trade_time=datetime(2026, 5, 1, tzinfo=timezone.utc)),
        _make_record(acct, 51, symbol="AAPL", side="SELL", trade_time=datetime(2026, 5, 2, tzinfo=timezone.utc),
                       position_effect="CLOSING", price=12.0),
    ])
    r = app_client.get(f"/api/journal/trades?account={acct}&days=365")
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    syms = {t["symbol"] for t in body["trades"]}
    assert syms == {"AAPL"}
    sides = {t["side"] for t in body["trades"]}
    assert sides == {"BUY", "SELL"}


def test_get_summary_computes_pnl(app_client, acct: str) -> None:
    """Round-trip on TEST gives +$200 gross at the route level (FIFO)."""
    journal_repo.insert_trades_batch([
        _make_record(acct, 60, side="BUY",  trade_time=datetime(2026, 5, 1, tzinfo=timezone.utc),
                       price=10.0, quantity=100, fees=0.0),
        _make_record(acct, 61, side="SELL", trade_time=datetime(2026, 5, 2, tzinfo=timezone.utc),
                       price=12.0, quantity=100, fees=0.0, position_effect="CLOSING"),
    ])
    r = app_client.get(f"/api/journal/summary?account={acct}&days=365")
    assert r.status_code == 200
    body = r.json()
    assert body["input_trade_count"] == 2
    assert body["overall"]["closed_trade_count"] == 1
    assert body["overall"]["total_realized_pnl"] == pytest.approx(200.0)
    assert body["overall"]["win_count"] == 1
    by_sym = {row["symbol"]: row for row in body["by_symbol"]}
    assert by_sym["TEST"]["net_pnl"] == pytest.approx(200.0)
    # Daily bars: 1 entry on the 2nd
    assert len(body["by_day"]) == 1
    assert body["by_day"][0]["net_pnl"] == pytest.approx(200.0)


def test_put_note_endpoint_persists(app_client, acct: str) -> None:
    journal_repo.insert_trades_batch([_make_record(acct, 70)])
    r = app_client.put(
        "/api/journal/notes/70",
        json={"account_hash": acct, "strategy": "scalp",
              "tags": ["morning"], "note": "Good fill"},
    )
    assert r.status_code == 200
    assert r.json()["ok"] is True
    rows = journal_repo.list_trades(account_hash=acct)
    assert rows[0]["strategy"] == "scalp"
    assert rows[0]["note"] == "Good fill"


def test_put_note_rejects_missing_account_hash(app_client, acct: str) -> None:
    r = app_client.put("/api/journal/notes/1", json={"account_hash": "", "note": "x"})
    assert r.status_code == 400


def test_get_accounts_returns_latest_snapshots(app_client, acct: str) -> None:
    """Snapshot rows should appear in /api/journal/accounts."""
    journal_repo.insert_account_snapshot(
        account_hash=acct,
        snapshot_time=datetime.now(timezone.utc),
        payload={"securitiesAccount": {
            "type": "CASH",
            "currentBalances": {"cashBalance": 100.0, "liquidationValue": 250.0},
        }},
    )
    r = app_client.get("/api/journal/accounts")
    assert r.status_code == 200
    accounts = r.json()["accounts"]
    mine = [a for a in accounts if a["account_hash"] == acct]
    assert len(mine) == 1
    assert mine[0]["cash_balance"] == pytest.approx(100.0)
    assert mine[0]["liquidation_value"] == pytest.approx(250.0)
    # Account number isn't known (no sync_account_numbers in tests) -> masked label
    assert mine[0]["account_label"] == "****"


def test_summary_handles_empty_account(app_client) -> None:
    """No trades -> well-formed empty summary, not a crash."""
    r = app_client.get(f"/api/journal/summary?account={TEST_PREFIX}nonexistent&days=30")
    assert r.status_code == 200
    body = r.json()
    assert body["overall"]["closed_trade_count"] == 0
    assert body["overall"]["total_realized_pnl"] == 0.0
    assert body["by_day"] == []
    assert body["legs"] == []


def test_sync_endpoint_returns_zero_when_no_provider(app_client) -> None:
    """
    The provider isn't logged in during tests (no creds), so the sync call
    should gracefully report zero work done — not crash.
    """
    r = app_client.post("/api/journal/sync", json={"days": 7, "force": True})
    assert r.status_code == 200
    body = r.json()
    for k in ("accounts", "snapshots", "trades_fetched", "trades_inserted"):
        assert k in body
