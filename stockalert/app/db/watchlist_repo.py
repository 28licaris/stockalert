"""
ClickHouse repository for watchlists and their members.

This module is a *pure* data layer:
- No I/O outside ClickHouse.
- No business logic (subscription management, backfill, etc. live elsewhere).
- All operations are idempotent and safe to retry.

Soft-delete model
-----------------
Both `watchlists` and `watchlist_members` use `ReplacingMergeTree(version)`
keyed on the natural identifier (name / (name, symbol)). To "delete" a row
we insert a new version with `is_active = 0`. This keeps full history so a
future LLM/agent can answer "what was in this watchlist last week?".

Query pattern
-------------
Reads use `FINAL` to collapse versions, then `WHERE is_active = 1` to hide
soft-deleted rows. For larger tables we would expose a non-FINAL variant,
but for watchlists (a handful of rows) FINAL is cheap and correct.
"""
from __future__ import annotations

import time
from typing import Iterable, Optional

from app.db.client import get_client

VALID_KINDS = {"user", "baseline", "adhoc"}


def _now_version() -> int:
    """Monotonic-enough version key for ReplacingMergeTree (milliseconds since epoch)."""
    return time.time_ns() // 1_000_000


def _normalize_name(name: str) -> str:
    n = (name or "").strip()
    if not n:
        raise ValueError("watchlist name must be non-empty")
    return n


def _normalize_symbols(symbols: Iterable[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for s in symbols or []:
        ss = (s or "").strip().upper()
        if ss and ss not in seen:
            seen.add(ss)
            out.append(ss)
    return out


# ----- Watchlists -----


def list_watchlists(include_inactive: bool = False) -> list[dict]:
    """Return all watchlists (active by default), sorted by name."""
    client = get_client()
    where = "" if include_inactive else "WHERE is_active = 1"
    result = client.query(
        f"""
        SELECT name, kind, description, is_active, updated_at
        FROM watchlists FINAL
        {where}
        ORDER BY name
        """
    )
    rows = []
    for r in result.result_rows:
        rows.append(
            {
                "name": r[0],
                "kind": r[1],
                "description": r[2],
                "is_active": bool(r[3]),
                "updated_at": r[4],
            }
        )
    return rows


def get_watchlist(name: str) -> Optional[dict]:
    """Return a single watchlist (active or not), or None."""
    name = _normalize_name(name)
    client = get_client()
    result = client.query(
        """
        SELECT name, kind, description, is_active, updated_at
        FROM watchlists FINAL
        WHERE name = {n:String}
        """,
        parameters={"n": name},
    )
    if not result.result_rows:
        return None
    r = result.result_rows[0]
    return {
        "name": r[0],
        "kind": r[1],
        "description": r[2],
        "is_active": bool(r[3]),
        "updated_at": r[4],
    }


def create_watchlist(name: str, kind: str = "user", description: str = "") -> dict:
    """
    Create (or reactivate) a watchlist. Idempotent: calling on an existing active
    watchlist just bumps `updated_at`. Returns the resulting watchlist row.
    """
    name = _normalize_name(name)
    if kind not in VALID_KINDS:
        raise ValueError(f"invalid kind '{kind}', allowed: {sorted(VALID_KINDS)}")
    client = get_client()
    client.insert(
        "watchlists",
        [[name, kind, description or "", 1, _now_version()]],
        column_names=["name", "kind", "description", "is_active", "version"],
    )
    wl = get_watchlist(name)
    assert wl is not None  # we just inserted
    return wl


def delete_watchlist(name: str) -> bool:
    """
    Soft-delete: mark `is_active = 0`. Members remain in `watchlist_members` but
    `list_members(name)` will treat the deleted watchlist as empty. Returns True
    if the watchlist existed (whether or not it was already inactive).
    """
    name = _normalize_name(name)
    existing = get_watchlist(name)
    if existing is None:
        return False
    client = get_client()
    client.insert(
        "watchlists",
        [[name, existing["kind"], existing["description"], 0, _now_version()]],
        column_names=["name", "kind", "description", "is_active", "version"],
    )
    return True


def rename_watchlist(old: str, new: str) -> dict:
    """
    Rename by copying active members to `new`, then soft-deleting `old`.
    Atomic enough for our purposes (no concurrent watchlist edits expected).
    """
    old = _normalize_name(old)
    new = _normalize_name(new)
    if old == new:
        wl = get_watchlist(new)
        if wl is None:
            raise ValueError(f"watchlist '{old}' does not exist")
        return wl
    src = get_watchlist(old)
    if src is None or not src["is_active"]:
        raise ValueError(f"watchlist '{old}' does not exist or is inactive")
    if get_watchlist(new) is not None and get_watchlist(new)["is_active"]:
        raise ValueError(f"watchlist '{new}' already exists")
    create_watchlist(new, kind=src["kind"], description=src["description"])
    members = list_members(old)
    if members:
        add_members(new, members)
    delete_watchlist(old)
    return get_watchlist(new) or {}


# ----- Members -----


def list_members(name: str) -> list[str]:
    """Return the *active* symbols in a watchlist, sorted."""
    name = _normalize_name(name)
    client = get_client()
    result = client.query(
        """
        SELECT symbol
        FROM watchlist_members FINAL
        WHERE watchlist_name = {n:String} AND is_active = 1
        ORDER BY symbol
        """,
        parameters={"n": name},
    )
    return [r[0] for r in result.result_rows]


def add_members(name: str, symbols: Iterable[str]) -> list[str]:
    """
    Add symbols to a watchlist. Idempotent. Returns the list of symbols that
    were *newly* activated (already-active symbols are not in the return value).
    Auto-creates the watchlist (as 'user' kind) if it does not exist.
    """
    name = _normalize_name(name)
    syms = _normalize_symbols(symbols)
    if not syms:
        return []
    if get_watchlist(name) is None:
        create_watchlist(name)

    existing = set(list_members(name))
    newly = [s for s in syms if s not in existing]
    if not newly and not syms:
        return []

    client = get_client()
    version = _now_version()
    rows = [[name, s, 1, version] for s in syms]  # re-assert all (idempotent)
    client.insert(
        "watchlist_members",
        rows,
        column_names=["watchlist_name", "symbol", "is_active", "version"],
    )
    return newly


def remove_members(name: str, symbols: Iterable[str]) -> list[str]:
    """
    Remove symbols from a watchlist. Idempotent. Returns the list of symbols
    that were *actually* deactivated (i.e. were active before this call).
    """
    name = _normalize_name(name)
    syms = _normalize_symbols(symbols)
    if not syms or get_watchlist(name) is None:
        return []

    active = set(list_members(name))
    to_remove = [s for s in syms if s in active]
    if not to_remove:
        return []

    client = get_client()
    version = _now_version()
    rows = [[name, s, 0, version] for s in to_remove]
    client.insert(
        "watchlist_members",
        rows,
        column_names=["watchlist_name", "symbol", "is_active", "version"],
    )
    return to_remove


def list_all_active_symbols(kinds: Optional[Iterable[str]] = None) -> set[str]:
    """
    Return the union of active members across all active watchlists, optionally
    filtered by watchlist `kinds` (e.g. `{'baseline'}`, `{'user','adhoc'}`).
    Used by the streamer to compute the global subscription set.
    """
    client = get_client()
    if kinds is not None:
        kinds_list = sorted({k for k in kinds if k in VALID_KINDS})
        if not kinds_list:
            return set()
        result = client.query(
            """
            SELECT DISTINCT m.symbol
            FROM watchlist_members AS m FINAL
            INNER JOIN (
                SELECT name FROM watchlists FINAL
                WHERE is_active = 1 AND kind IN {kinds:Array(String)}
            ) AS w ON m.watchlist_name = w.name
            WHERE m.is_active = 1
            """,
            parameters={"kinds": kinds_list},
        )
    else:
        result = client.query(
            """
            SELECT DISTINCT m.symbol
            FROM watchlist_members AS m FINAL
            INNER JOIN (
                SELECT name FROM watchlists FINAL WHERE is_active = 1
            ) AS w ON m.watchlist_name = w.name
            WHERE m.is_active = 1
            """
        )
    return {r[0] for r in result.result_rows}


def watchlists_containing(symbol: str) -> list[str]:
    """Return the names of active watchlists that currently contain `symbol`."""
    sym = (symbol or "").strip().upper()
    if not sym:
        return []
    client = get_client()
    result = client.query(
        """
        SELECT DISTINCT m.watchlist_name
        FROM watchlist_members AS m FINAL
        INNER JOIN (
            SELECT name FROM watchlists FINAL WHERE is_active = 1
        ) AS w ON m.watchlist_name = w.name
        WHERE m.is_active = 1 AND m.symbol = {s:String}
        ORDER BY m.watchlist_name
        """,
        parameters={"s": sym},
    )
    return [r[0] for r in result.result_rows]
