"""
Integration tests for app.db.watchlist_repo.

These run against a real ClickHouse (docker-compose) so we exercise the
ReplacingMergeTree FINAL semantics that matter most in production. All
test fixtures use a `__test_` prefix and clean up after themselves so
the suite is safe to re-run.
"""
from __future__ import annotations

import uuid

import pytest

from app.db import watchlist_repo
from app.db.client import get_client


TEST_PREFIX = "__test_wl_"


def _wipe_watchlist(name: str) -> None:
    """Hard-delete every row for `name` from both tables. Used only by tests.

    Safety: refuses to operate on watchlists whose names do not start with
    `__test_`, so an accidental cleanup call with a real name (like 'default')
    cannot destroy production data.
    """
    if not name.startswith("__test_"):
        raise ValueError(
            f"_wipe_watchlist refused to act on non-test watchlist name '{name}'."
        )
    client = get_client()
    client.command(
        "ALTER TABLE watchlists DELETE WHERE name = {n:String}",
        parameters={"n": name},
    )
    client.command(
        "ALTER TABLE watchlist_members DELETE WHERE watchlist_name = {n:String}",
        parameters={"n": name},
    )


@pytest.fixture
def wl_name(clickhouse_ready) -> str:
    """Yield a unique watchlist name and hard-clean both tables after the test."""
    name = f"{TEST_PREFIX}{uuid.uuid4().hex[:8]}"
    yield name
    _wipe_watchlist(name)


def test_create_and_list(wl_name: str) -> None:
    created = watchlist_repo.create_watchlist(wl_name, kind="user", description="hi")
    assert created["name"] == wl_name
    assert created["kind"] == "user"
    assert created["description"] == "hi"
    assert created["is_active"] is True

    names = [w["name"] for w in watchlist_repo.list_watchlists()]
    assert wl_name in names


def test_add_remove_members_is_idempotent(wl_name: str) -> None:
    watchlist_repo.create_watchlist(wl_name)

    newly = watchlist_repo.add_members(wl_name, ["AAPL", "msft", "  AAPL ", ""])
    assert sorted(newly) == ["AAPL", "MSFT"]
    assert watchlist_repo.list_members(wl_name) == ["AAPL", "MSFT"]

    # Re-adding existing symbols is a no-op for `newly` but still safe.
    again = watchlist_repo.add_members(wl_name, ["AAPL"])
    assert again == []
    assert watchlist_repo.list_members(wl_name) == ["AAPL", "MSFT"]

    removed = watchlist_repo.remove_members(wl_name, ["aapl"])
    assert removed == ["AAPL"]
    assert watchlist_repo.list_members(wl_name) == ["MSFT"]

    # Removing already-removed symbol is a no-op.
    assert watchlist_repo.remove_members(wl_name, ["AAPL"]) == []


def test_re_add_after_remove(wl_name: str) -> None:
    watchlist_repo.create_watchlist(wl_name)
    watchlist_repo.add_members(wl_name, ["TSLA"])
    watchlist_repo.remove_members(wl_name, ["TSLA"])
    assert watchlist_repo.list_members(wl_name) == []

    newly = watchlist_repo.add_members(wl_name, ["TSLA"])
    assert newly == ["TSLA"]
    assert watchlist_repo.list_members(wl_name) == ["TSLA"]


def test_soft_delete_watchlist(wl_name: str) -> None:
    watchlist_repo.create_watchlist(wl_name)
    watchlist_repo.add_members(wl_name, ["NVDA"])

    assert watchlist_repo.delete_watchlist(wl_name) is True

    # Active list no longer shows the deleted watchlist.
    assert wl_name not in [w["name"] for w in watchlist_repo.list_watchlists()]

    # But the row still exists in history.
    history = [w["name"] for w in watchlist_repo.list_watchlists(include_inactive=True)]
    assert wl_name in history

    # And it's reported as inactive by get_watchlist.
    wl = watchlist_repo.get_watchlist(wl_name)
    assert wl is not None and wl["is_active"] is False


def test_list_all_active_symbols_filters_by_kind(clickhouse_ready) -> None:
    a = f"{TEST_PREFIX}{uuid.uuid4().hex[:8]}"
    b = f"{TEST_PREFIX}{uuid.uuid4().hex[:8]}"
    try:
        watchlist_repo.create_watchlist(a, kind="user")
        watchlist_repo.create_watchlist(b, kind="baseline")
        watchlist_repo.add_members(a, ["AAPL", "MSFT"])
        watchlist_repo.add_members(b, ["MSFT", "GOOGL"])

        union = watchlist_repo.list_all_active_symbols()
        assert {"AAPL", "MSFT", "GOOGL"}.issubset(union)

        baseline_only = watchlist_repo.list_all_active_symbols(kinds={"baseline"})
        assert "AAPL" not in baseline_only
        assert {"MSFT", "GOOGL"}.issubset(baseline_only)
    finally:
        _wipe_watchlist(a)
        _wipe_watchlist(b)


def test_watchlists_containing(clickhouse_ready) -> None:
    a = f"{TEST_PREFIX}{uuid.uuid4().hex[:8]}"
    b = f"{TEST_PREFIX}{uuid.uuid4().hex[:8]}"
    try:
        watchlist_repo.create_watchlist(a)
        watchlist_repo.create_watchlist(b)
        watchlist_repo.add_members(a, ["SPY"])
        watchlist_repo.add_members(b, ["SPY", "QQQ"])

        names = watchlist_repo.watchlists_containing("SPY")
        assert a in names and b in names
        assert watchlist_repo.watchlists_containing("QQQ") == [b]
    finally:
        _wipe_watchlist(a)
        _wipe_watchlist(b)


def test_rename_preserves_members(wl_name: str) -> None:
    old = wl_name
    new = old + "_renamed"
    try:
        watchlist_repo.create_watchlist(old)
        watchlist_repo.add_members(old, ["AMD", "INTC"])

        renamed = watchlist_repo.rename_watchlist(old, new)
        assert renamed["name"] == new
        assert sorted(watchlist_repo.list_members(new)) == ["AMD", "INTC"]

        # Old watchlist should be inactive.
        wl_old = watchlist_repo.get_watchlist(old)
        assert wl_old is not None and wl_old["is_active"] is False
    finally:
        _wipe_watchlist(new)
