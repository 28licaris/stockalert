"""
Unit tests for app.services.watchlist_service.WatchlistService.

These are pure unit tests: the watchlist_repo is replaced with an in-memory
fake, and the data provider is a `FakeDataProvider` that records calls.
The real repo's FINAL/soft-delete semantics are covered by
`tests/test_watchlist_repo.py`.
"""
from __future__ import annotations

from typing import Callable, Optional

import pandas as pd
import pytest

from app.providers.base import DataProvider
from app.services import watchlist_service as wls_module
from app.services.watchlist_service import WatchlistService


# ----- Fakes -----


class FakeDataProvider(DataProvider):
    """In-process provider that records subscribe/unsubscribe calls."""

    def __init__(self) -> None:
        self.subscribed: set[str] = set()
        self.subscribe_calls: list[list[str]] = []
        self.unsubscribe_calls: list[list[str]] = []
        self.stopped = False
        self._callback: Optional[Callable] = None

    def start_stream(self) -> None:
        pass

    def stop_stream(self) -> None:
        self.stopped = True

    def subscribe_bars(self, callback, tickers: list[str]) -> None:
        self._callback = callback
        for t in tickers:
            self.subscribed.add(t)
        self.subscribe_calls.append(list(tickers))

    def unsubscribe_bars(self, tickers: list[str]) -> None:
        for t in tickers:
            self.subscribed.discard(t)
        self.unsubscribe_calls.append(list(tickers))

    async def historical_df(self, symbol, start, end, timeframe="1Min"):
        return pd.DataFrame()


class FakeRepo:
    """In-memory shim that mimics app.db.watchlist_repo's surface area."""

    def __init__(self) -> None:
        # name -> {kind, description, is_active}
        self.watchlists: dict[str, dict] = {}
        # name -> set[symbol]
        self.members: dict[str, set[str]] = {}

    # ---- watchlists ----

    def list_watchlists(self, include_inactive: bool = False) -> list[dict]:
        out = []
        for name, meta in self.watchlists.items():
            if not include_inactive and not meta["is_active"]:
                continue
            out.append({"name": name, **meta, "updated_at": None})
        out.sort(key=lambda r: r["name"])
        return out

    def get_watchlist(self, name: str) -> Optional[dict]:
        meta = self.watchlists.get(name)
        if meta is None:
            return None
        return {"name": name, **meta, "updated_at": None}

    def create_watchlist(self, name: str, kind: str = "user", description: str = "") -> dict:
        self.watchlists[name] = {"kind": kind, "description": description, "is_active": True}
        self.members.setdefault(name, set())
        return self.get_watchlist(name)  # type: ignore[return-value]

    def delete_watchlist(self, name: str) -> bool:
        if name not in self.watchlists:
            return False
        self.watchlists[name]["is_active"] = False
        return True

    def rename_watchlist(self, old: str, new: str) -> dict:
        meta = self.watchlists.pop(old)
        meta["is_active"] = True
        self.watchlists[new] = meta
        self.members[new] = self.members.pop(old, set())
        return self.get_watchlist(new)  # type: ignore[return-value]

    # ---- members ----

    def list_members(self, name: str) -> list[str]:
        if not self.watchlists.get(name, {}).get("is_active"):
            return []
        return sorted(self.members.get(name, set()))

    def add_members(self, name: str, symbols) -> list[str]:
        if name not in self.watchlists:
            self.create_watchlist(name)
        existing = self.members.setdefault(name, set())
        newly = []
        for s in symbols or []:
            ss = (s or "").strip().upper()
            if ss and ss not in existing:
                existing.add(ss)
                newly.append(ss)
        return newly

    def remove_members(self, name: str, symbols) -> list[str]:
        existing = self.members.get(name, set())
        removed = []
        for s in symbols or []:
            ss = (s or "").strip().upper()
            if ss in existing:
                existing.discard(ss)
                removed.append(ss)
        return removed

    def list_all_active_symbols(self, kinds=None) -> set[str]:
        out: set[str] = set()
        for name, meta in self.watchlists.items():
            if not meta["is_active"]:
                continue
            if kinds is not None and meta["kind"] not in set(kinds):
                continue
            out |= self.members.get(name, set())
        return out

    def watchlists_containing(self, symbol: str) -> list[str]:
        sym = (symbol or "").strip().upper()
        return sorted(
            n for n, syms in self.members.items()
            if sym in syms and self.watchlists.get(n, {}).get("is_active")
        )


# ----- Fixture -----


@pytest.fixture
def svc(monkeypatch) -> WatchlistService:
    fake_repo = FakeRepo()
    fake_prov = FakeDataProvider()

    monkeypatch.setattr(wls_module, "watchlist_repo", fake_repo)
    monkeypatch.setattr(wls_module, "get_stream_provider", lambda: fake_prov)

    # backfill=None disables auto-enqueue so these tests don't pull in the
    # real loader/provider. Backfill behavior is covered in test_backfill_service.py.
    s = WatchlistService(backfill=None)
    s._provider = fake_prov  # bypass lazy init
    s._fake_repo = fake_repo  # type: ignore[attr-defined]
    s._fake_prov = fake_prov  # type: ignore[attr-defined]
    return s


# ----- start() -----


@pytest.mark.asyncio
async def test_start_subscribes_to_all_active_members(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist("a")
    svc._fake_repo.create_watchlist("b")
    svc._fake_repo.add_members("a", ["AAPL", "MSFT"])
    svc._fake_repo.add_members("b", ["MSFT", "GOOGL"])

    await svc.start()

    assert svc._fake_prov.subscribed == {"AAPL", "GOOGL", "MSFT"}
    assert svc._refcount == {"AAPL": 1, "MSFT": 2, "GOOGL": 1}
    # MSFT was de-duplicated, so we expect exactly one subscribe call carrying all three.
    assert svc._fake_prov.subscribe_calls == [["AAPL", "GOOGL", "MSFT"]]


@pytest.mark.asyncio
async def test_start_keeps_baseline_subscribed(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist("base", kind="baseline")
    svc._fake_repo.add_members("base", ["SPY", "QQQ"])

    await svc.start()

    assert svc._fake_prov.subscribed == {"SPY", "QQQ"}
    assert svc._baseline == {"SPY", "QQQ"}
    # Baseline symbols are NOT in the refcount map - they are tracked separately.
    assert svc._refcount == {}


# ----- add_members() -----


@pytest.mark.asyncio
async def test_add_member_subscribes_once(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist("a")
    await svc.start()
    assert svc._fake_prov.subscribed == set()

    result = svc.add_members("a", ["NVDA"])
    assert result["added"] == ["NVDA"]
    assert "NVDA" in svc._fake_prov.subscribed

    # Re-adding is a no-op for newly-added; provider not called again.
    result = svc.add_members("a", ["NVDA"])
    assert result["added"] == []
    assert len(svc._fake_prov.subscribe_calls) == 1


@pytest.mark.asyncio
async def test_same_symbol_in_two_watchlists_subscribed_once(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist("a")
    svc._fake_repo.create_watchlist("b")
    await svc.start()

    svc.add_members("a", ["AMZN"])
    svc.add_members("b", ["AMZN"])

    flat_subs = [s for batch in svc._fake_prov.subscribe_calls for s in batch]
    assert flat_subs == ["AMZN"]  # only one provider subscribe call
    assert svc._refcount["AMZN"] == 2


@pytest.mark.asyncio
async def test_add_normalizes_and_dedupes_input(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist("a")
    await svc.start()
    result = svc.add_members("a", ["aapl", "  AAPL ", "msft", "", None])  # type: ignore[list-item]
    assert sorted(result["added"]) == ["AAPL", "MSFT"]


# ----- remove_members() -----


@pytest.mark.asyncio
async def test_remove_from_one_watchlist_keeps_subscribed_if_in_other(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist("a")
    svc._fake_repo.create_watchlist("b")
    svc._fake_repo.add_members("a", ["TSLA"])
    svc._fake_repo.add_members("b", ["TSLA"])
    await svc.start()
    assert "TSLA" in svc._fake_prov.subscribed

    svc.remove_members("a", ["TSLA"])
    assert "TSLA" in svc._fake_prov.subscribed  # b still owns it
    assert svc._refcount["TSLA"] == 1

    svc.remove_members("b", ["TSLA"])
    assert "TSLA" not in svc._fake_prov.subscribed
    assert "TSLA" not in svc._refcount


@pytest.mark.asyncio
async def test_baseline_symbol_not_unsubscribed_when_user_watchlist_drops_it(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist("base", kind="baseline")
    svc._fake_repo.create_watchlist("user")
    svc._fake_repo.add_members("base", ["IWM"])
    svc._fake_repo.add_members("user", ["IWM"])
    await svc.start()
    assert "IWM" in svc._fake_prov.subscribed
    assert svc._refcount["IWM"] == 1  # only the 'user' watchlist contributes to refcount

    svc.remove_members("user", ["IWM"])
    assert "IWM" in svc._fake_prov.subscribed  # baseline still owns it
    assert "IWM" in svc._baseline
    assert svc._refcount.get("IWM", 0) == 0


# ----- delete_watchlist() -----


@pytest.mark.asyncio
async def test_delete_watchlist_unsubscribes_only_orphans(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist("a")
    svc._fake_repo.create_watchlist("b")
    svc._fake_repo.add_members("a", ["F", "GM"])  # F overlaps, GM is exclusive to a
    svc._fake_repo.add_members("b", ["F"])
    await svc.start()
    assert {"F", "GM"}.issubset(svc._fake_prov.subscribed)

    assert svc.delete_watchlist("a") is True
    assert "GM" not in svc._fake_prov.subscribed  # orphaned
    assert "F" in svc._fake_prov.subscribed       # still owned by b
    assert svc._refcount.get("GM", 0) == 0
    assert svc._refcount["F"] == 1


# ----- legacy shim -----


@pytest.mark.asyncio
async def test_legacy_add_remove_target_default_watchlist(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist(wls_module.DEFAULT_WATCHLIST_NAME)
    await svc.start()

    result = svc.add(["NEW1", "NEW2"])
    assert sorted(result["added"]) == ["NEW1", "NEW2"]
    assert "NEW1" in svc._fake_prov.subscribed

    result = svc.remove(["NEW1"])
    assert result["removed"] == ["NEW1"]
    assert "NEW1" not in svc._fake_prov.subscribed

    st = svc.status()
    assert st["symbols"] == ["NEW2"]
    assert st["symbol_count"] == 1
    assert "NEW2" in st["streaming_symbols"]


@pytest.mark.asyncio
async def test_stop_unsubscribes_everything(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist("a")
    svc._fake_repo.add_members("a", ["X", "Y"])
    await svc.start()
    assert svc._fake_prov.subscribed == {"X", "Y"}

    await svc.stop()
    assert svc._fake_prov.subscribed == set()
    assert svc._fake_prov.stopped is True
