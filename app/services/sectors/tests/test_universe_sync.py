"""Tests for the tracked-instruments → universe reconciler."""
from __future__ import annotations

import asyncio

import app.services.sectors.theme_store as theme_store_mod
import app.services.stream as stream_mod
import app.services.universe as universe_mod
from app.services.sectors import universe_sync
from app.services.sectors.schemas import ThemeRecord


def _stub_theme(monkeypatch):
    """Pin the theme store to one basket so tracked_symbols is deterministic
    (no live ClickHouse needed)."""
    monkeypatch.setattr(
        theme_store_mod, "list_themes",
        lambda include_inactive=False: [
            ThemeRecord(theme_id="miners", name="Miners", label="MIN",
                        members=["NEM", "GOLD", "AG"], benchmark="SPY", is_active=True),
        ],
    )


def test_tracked_symbols_covers_sectors_and_theme_members(monkeypatch):
    _stub_theme(monkeypatch)
    syms = set(universe_sync.tracked_symbols())
    assert {"XLK", "XLV", "SPY"} <= syms      # built-in sector ETFs + benchmark
    assert {"NEM", "GOLD", "AG"} <= syms      # stored theme members
    assert "miners" not in syms               # the theme id is not a tradable symbol


def test_ensure_adds_only_missing(monkeypatch):
    _stub_theme(monkeypatch)
    monkeypatch.setattr(universe_mod, "get_active_universe", lambda: ["XLK", "SPY"])
    added: list[str] = []

    class _FakeStream:
        def add(self, sym, **kw):  # noqa: ANN001
            added.append(sym)
            return {"changed": [sym]}

    monkeypatch.setattr(stream_mod, "stream_service", _FakeStream())

    res = asyncio.run(universe_sync.ensure_tracked_in_universe(tip_fill=False, deep_history=False))

    assert "XLK" not in added                  # already active → skipped
    assert {"XLV", "NEM", "AG"} <= set(added)  # missing → added
    assert set(res["added"]) == set(added)
    assert res["tracked"] == len(universe_sync.tracked_symbols())


def test_ensure_noop_when_all_active(monkeypatch):
    _stub_theme(monkeypatch)
    monkeypatch.setattr(
        universe_mod, "get_active_universe",
        lambda: list(universe_sync.tracked_symbols()),
    )
    called = {"add": 0}

    class _FakeStream:
        def add(self, *a, **k):
            called["add"] += 1

    monkeypatch.setattr(stream_mod, "stream_service", _FakeStream())

    res = asyncio.run(universe_sync.ensure_tracked_in_universe(tip_fill=False, deep_history=False))
    assert res["added"] == []
    assert called["add"] == 0  # nothing to do — cheap no-op
