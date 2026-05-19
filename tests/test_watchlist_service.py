"""
Unit tests for the post-FE-CONTRACTS-4 WatchlistService.

WatchlistService is now CRUD-only: it no longer owns Schwab
subscriptions, refcounts, or baselines. Per docs/frontend_api_contracts.md
§10.1, those concerns moved to `StreamService`. These tests verify:

  - Pure CRUD passes through to the repo correctly.
  - `add_members` calls `stream_service.ensure_streaming` (auto-extend).
  - `remove_members` / `delete_watchlist` do NOT touch the stream
    (sticky-universe invariant).
  - `status()` composes legacy + stream fields without owning state.

Subscription mechanics are covered in `test_stream_service.py`.
"""
from __future__ import annotations

from typing import Optional

import pytest

from app.services.live import watchlist_service as wls_module
from app.services.live.watchlist_service import WatchlistService


# ----- Fakes -----


class FakeStreamService:
    """Records ensure_streaming/remove/status calls. NEVER subscribes."""

    def __init__(self) -> None:
        self.universe: set[str] = set()
        self.ensure_calls: list[tuple[list[str], str]] = []
        self.remove_calls: list[str] = []

    def ensure_streaming(self, symbols, *, added_by: str = "", source: str = "watchlist") -> list[str]:
        added = []
        for s in symbols:
            ss = (s or "").strip().upper()
            if ss and ss not in self.universe:
                self.universe.add(ss)
                added.append(ss)
        self.ensure_calls.append((list(added), source))
        return added

    def is_streaming(self, symbol: str) -> bool:
        return (symbol or "").strip().upper() in self.universe

    def remove(self, symbol: str, *, owner_id: Optional[str] = None) -> dict:
        ss = (symbol or "").strip().upper()
        was = ss in self.universe
        self.universe.discard(ss)
        self.remove_calls.append(ss)
        return {"operation": "remove", "changed": [ss] if was else [], "items": [], "count": len(self.universe)}

    def status(self) -> dict:
        return {
            "started": True,
            "provider": "fake-stream",
            "provider_ready": True,
            "provider_error": None,
            "streaming_count": len(self.universe),
            "streaming_symbols": sorted(self.universe),
            "universe_count": len(self.universe),
        }


class FakeRepo:
    """In-memory shim that mimics app.db.watchlist_repo's surface."""

    def __init__(self) -> None:
        self.watchlists: dict[str, dict] = {}
        self.members: dict[str, set[str]] = {}

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
    fake_stream = FakeStreamService()

    monkeypatch.setattr(wls_module, "watchlist_repo", fake_repo)

    # The auto-extend hook lazy-imports `app.services.stream` inside
    # add_members. Replace the module's `stream_service` so the hook
    # talks to our fake.
    import app.services.stream as stream_module
    monkeypatch.setattr(stream_module, "stream_service", fake_stream)

    s = WatchlistService(backfill=None)
    s._fake_repo = fake_repo  # type: ignore[attr-defined]
    s._fake_stream = fake_stream  # type: ignore[attr-defined]
    return s


# ----- start/stop are no-ops -----


@pytest.mark.asyncio
async def test_start_is_idempotent_and_no_subscription_state(svc: WatchlistService) -> None:
    await svc.start()
    await svc.start()  # second call: no error
    assert not hasattr(svc, "_refcount")
    assert not hasattr(svc, "_baseline")
    assert not hasattr(svc, "_subscribed")
    assert svc._started is True


@pytest.mark.asyncio
async def test_stop_does_not_unsubscribe(svc: WatchlistService) -> None:
    await svc.start()
    await svc.stop()
    # WatchlistService should never have touched the stream during stop.
    assert svc._fake_stream.remove_calls == []


# ----- add_members auto-extends the stream universe -----


def test_add_members_auto_extends_stream(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist("a")
    result = svc.add_members("a", ["AAPL", "MSFT"])
    assert sorted(result["added"]) == ["AAPL", "MSFT"]

    # ensure_streaming was called once with both new symbols.
    assert svc._fake_stream.ensure_calls == [(["AAPL", "MSFT"], "watchlist:a")]
    assert svc._fake_stream.universe == {"AAPL", "MSFT"}


def test_add_member_already_in_stream_is_noop_on_extend(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist("a")
    # Pre-populate the stream so AAPL is already known.
    svc._fake_stream.universe.add("AAPL")

    result = svc.add_members("a", ["AAPL"])
    assert result["added"] == ["AAPL"]

    # ensure_streaming was called but returned [] (no promotions).
    assert svc._fake_stream.ensure_calls == [([], "watchlist:a")]


def test_add_member_idempotent(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist("a")
    svc.add_members("a", ["NVDA"])
    # Re-adding the same symbol returns no new additions.
    result = svc.add_members("a", ["NVDA"])
    assert result["added"] == []


# ----- remove_members is sticky (does NOT touch the stream) -----


def test_remove_members_does_not_touch_stream(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist("a")
    svc.add_members("a", ["TSLA"])
    assert "TSLA" in svc._fake_stream.universe

    result = svc.remove_members("a", ["TSLA"])
    assert result["removed"] == ["TSLA"]
    # Stream universe is sticky — TSLA is still being streamed.
    assert "TSLA" in svc._fake_stream.universe
    assert svc._fake_stream.remove_calls == []


def test_remove_member_from_one_of_two_watchlists_keeps_streaming(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist("a")
    svc._fake_repo.create_watchlist("b")
    svc.add_members("a", ["GOOG"])
    svc.add_members("b", ["GOOG"])
    svc.remove_members("a", ["GOOG"])
    svc.remove_members("b", ["GOOG"])
    # Even after removing from BOTH watchlists, the stream universe
    # still contains GOOG (only StreamService.remove can evict it).
    assert "GOOG" in svc._fake_stream.universe


# ----- delete_watchlist is sticky too -----


def test_delete_watchlist_does_not_touch_stream(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist("a")
    svc.add_members("a", ["AMD"])
    assert svc._fake_stream.universe == {"AMD"}

    deleted = svc.delete_watchlist("a")
    assert deleted is True
    # Stream universe is sticky.
    assert svc._fake_stream.universe == {"AMD"}
    assert svc._fake_stream.remove_calls == []


# ----- status() composes legacy + stream fields -----


def test_status_delegates_streaming_fields_to_stream_service(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist(wls_module.DEFAULT_WATCHLIST_NAME)
    svc.add_members(wls_module.DEFAULT_WATCHLIST_NAME, ["X", "Y"])

    st = svc.status()
    assert st["symbols"] == ["X", "Y"]
    assert st["symbol_count"] == 2
    assert sorted(st["streaming_symbols"]) == ["X", "Y"]
    assert st["subscribed_count"] == 2
    # Legacy fields kept at zero (refcount/baseline gone).
    assert st["baseline_count"] == 0
    assert st["refcounted_count"] == 0


# ----- legacy shim -----


def test_legacy_add_remove_target_default_watchlist(svc: WatchlistService) -> None:
    svc._fake_repo.create_watchlist(wls_module.DEFAULT_WATCHLIST_NAME)
    result = svc.add(["NEW1", "NEW2"])
    assert sorted(result["added"]) == ["NEW1", "NEW2"]

    result = svc.remove(["NEW1"])
    assert result["removed"] == ["NEW1"]
    # Stream is sticky — NEW1 still streamed even though removed from watchlist.
    assert "NEW1" in svc._fake_stream.universe
