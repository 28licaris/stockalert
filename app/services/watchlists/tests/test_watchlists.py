"""Per-user watchlists: repository scoping + pretend-position returns.

Runs against in-memory sqlite (same SQLAlchemy models the Postgres
migration creates) — exercises the real queries, not fakes.
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.services.identity.models import IdentityBase
from app.services.watchlists.repository import WatchlistsRepository
from app.services.watchlists.service import WatchlistsService


@pytest.fixture()
def service(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    IdentityBase.metadata.create_all(engine)
    repo = WatchlistsRepository(sessionmaker(bind=engine))
    svc = WatchlistsService(repo, default_qty=100.0)
    monkeypatch.setattr(
        WatchlistsService, "_latest_prices",
        staticmethod(lambda symbols: {s: {"AAPL": 210.0, "NVDA": 130.0}.get(s) for s in symbols
                                      if s in ("AAPL", "NVDA")}),
    )
    monkeypatch.setattr(
        WatchlistsService, "_ensure_streaming", staticmethod(lambda *a, **k: None)
    )
    return svc


def _user(svc) -> tuple:
    uid = uuid4()
    svc.ensure_user(uid, f"{uid}@test.local", "Test")
    return uid


def test_scoped_per_user(service):
    a, b = _user(service), _user(service)
    service.create(a, "growth")
    service.create(b, "growth")  # same name, different user — both fine
    service.add_symbols(a, "growth", ["AAPL"])
    assert [w.name for w in service.list(a)] == ["growth"]
    assert service.detail(a, "growth").n_members == 1
    assert service.detail(b, "growth").n_members == 0  # B cannot see A's members


def test_pretend_position_and_returns(service, monkeypatch):
    u = _user(service)
    service.create(u, "core")
    d = service.add_symbols(u, "core", ["AAPL"], quantity=50)
    m = d.members[0]
    assert (m.quantity, m.entry_price, m.current_price) == (50, 210.0, 210.0)
    # simulate the price moving: +$5 -> pnl 50 * 5 = 250, +2.38%
    monkeypatch.setattr(WatchlistsService, "_latest_prices",
                        staticmethod(lambda symbols: {"AAPL": 215.0}))
    d2 = service.detail(u, "core")
    assert d2.members[0].pnl_usd == pytest.approx(250.0)
    assert d2.members[0].pnl_pct == pytest.approx(5 / 210)
    assert d2.total_pnl_usd == pytest.approx(250.0)


def test_default_quantity_and_idempotent_readd(service, monkeypatch):
    u = _user(service)
    service.create(u, "w")
    d = service.add_symbols(u, "w", ["AAPL"])
    assert d.members[0].quantity == 100.0  # default
    # re-adding does NOT reset the original pretend position
    monkeypatch.setattr(WatchlistsService, "_latest_prices",
                        staticmethod(lambda symbols: {"AAPL": 999.0}))
    d2 = service.add_symbols(u, "w", ["AAPL"], quantity=7)
    assert d2.members[0].quantity == 100.0
    assert d2.members[0].entry_price == 210.0


def test_entry_price_backfill_when_missing(service, monkeypatch):
    u = _user(service)
    service.create(u, "w")
    d = service.add_symbols(u, "w", ["ZZZZ"])  # no price known at add time
    assert d.members[0].entry_price is None and d.members[0].pnl_usd is None
    monkeypatch.setattr(WatchlistsService, "_latest_prices",
                        staticmethod(lambda symbols: {"ZZZZ": 10.0}))
    d2 = service.detail(u, "w")
    assert d2.members[0].entry_price == 10.0  # backfilled at first sighting
    assert d2.members[0].pnl_usd == pytest.approx(0.0)


def test_remove_and_delete(service):
    u = _user(service)
    service.create(u, "w")
    service.add_symbols(u, "w", ["AAPL", "NVDA"])
    assert service.remove_symbol(u, "w", "AAPL") is True
    assert service.detail(u, "w").n_members == 1
    assert service.delete(u, "w") is True
    assert service.list(u) == []
