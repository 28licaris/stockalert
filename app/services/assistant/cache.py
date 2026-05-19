"""SQLite response cache for the Assistant service.

Pattern lifted from the trading `LLMAgent`
(`app/services/sim/strategies/llm_agent.py`) but kept in a **separate
SQLite file** so the strategy cache stays pristine and reproducible.

Key shape (sha256 of the canonical join):

    model
    + system_prompt_sha256
    + tool_schema_sha256
    + serialized_user_and_assistant_messages
    + serialized_tool_results_so_far
    + extended_thinking_flag

Any change to any of those invalidates the cached entry. There is no
"semantic" matching — identical inputs produce identical outputs;
that's the whole point.

Why SQLite (not in-memory): survives restarts, replays of a yesterday
conversation cost zero new API calls, and we keep the cache portable
(one file you can delete to start over). The trading `LLMAgent`
proved this pattern works for production workloads.

Thread-safety: `sqlite3.connect(..., check_same_thread=False)` plus a
threading.Lock on writes. Reads are concurrent-safe at SQLite's
default isolation level for our access pattern (lookup, then write).
The lock is the cheap correctness layer.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final, Iterable

logger = logging.getLogger(__name__)


# Bump when the on-disk schema changes (forces a re-create).
_SCHEMA_VERSION: Final[int] = 1


@dataclass(frozen=True, slots=True)
class CacheKeyInputs:
    """All inputs that feed into a cache key.

    Frozen by construction so the hash is computed from an immutable
    snapshot. Don't mutate the source dicts after passing them in.
    """

    model: str
    system_prompt_sha256: str
    tool_schema_sha256: str
    messages: list[dict[str, Any]]
    tool_results: list[dict[str, Any]]
    use_extended_thinking: bool

    def compute_key(self) -> str:
        """Deterministic sha256 over the canonical join.

        `sort_keys=True` on every json.dumps so dict ordering doesn't
        leak into the hash. Keep this implementation **stable** — if
        you change the join, bump `_SCHEMA_VERSION`.
        """
        joined = "\x1f".join(
            (
                self.model,
                self.system_prompt_sha256,
                self.tool_schema_sha256,
                json.dumps(self.messages, sort_keys=True, default=str),
                json.dumps(self.tool_results, sort_keys=True, default=str),
                "1" if self.use_extended_thinking else "0",
            )
        )
        return hashlib.sha256(joined.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CachedResponse:
    """A previously-computed assistant turn payload.

    The structure mirrors what `service.py` would otherwise stream
    fresh from Anthropic; reading it back yields byte-identical
    behavior (modulo timing).
    """

    cache_key: str
    payload: dict[str, Any]
    """Full response payload — text chunks, tool calls, usage."""

    tokens_in: int
    tokens_out: int
    cost_usd: float
    created_at: float
    """Unix epoch seconds when the entry was first written."""


class ResponseCache:
    """SQLite-backed (key -> cached response) store.

    Single-table schema. New deployments auto-create; opening an
    existing DB with an older schema_version logs a warning and
    re-creates (we don't migrate caches, we rebuild them).
    """

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._lock = threading.Lock()
        # check_same_thread=False because the assistant service runs
        # on the FastAPI threadpool — many request threads share one
        # connection. Writes are still serialized by `_lock`.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(
            str(self._path), check_same_thread=False, isolation_level=None
        )
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM kv WHERE key = 'schema_version'"
            ).fetchone() if self._table_exists("kv") else None
            existing = int(row[0]) if row else None
            if existing == _SCHEMA_VERSION:
                return
            if existing is not None:
                logger.warning(
                    "assistant cache: schema_version mismatch (have=%s want=%s); "
                    "rebuilding cache at %s",
                    existing, _SCHEMA_VERSION, self._path,
                )
                self._conn.execute("DROP TABLE IF EXISTS cache_entries")
                self._conn.execute("DROP TABLE IF EXISTS kv")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    cache_key   TEXT PRIMARY KEY,
                    payload     TEXT NOT NULL,
                    tokens_in   INTEGER NOT NULL,
                    tokens_out  INTEGER NOT NULL,
                    cost_usd    REAL NOT NULL,
                    created_at  REAL NOT NULL
                )
                """
            )
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT)"
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO kv (key, value) VALUES ('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
            logger.info(
                "assistant cache: initialized schema_version=%d at %s",
                _SCHEMA_VERSION, self._path,
            )

    def _table_exists(self, name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone()
        return row is not None

    def lookup(self, key: str) -> CachedResponse | None:
        """Return the entry for `key`, or None if not cached."""
        row = self._conn.execute(
            """
            SELECT cache_key, payload, tokens_in, tokens_out, cost_usd, created_at
            FROM cache_entries WHERE cache_key = ?
            """,
            (key,),
        ).fetchone()
        if row is None:
            return None
        return CachedResponse(
            cache_key=row[0],
            payload=json.loads(row[1]),
            tokens_in=row[2],
            tokens_out=row[3],
            cost_usd=row[4],
            created_at=row[5],
        )

    def store(
        self,
        *,
        key: str,
        payload: dict[str, Any],
        tokens_in: int,
        tokens_out: int,
        cost_usd: float,
    ) -> CachedResponse:
        """Write (or overwrite) an entry. Returns the stored shape."""
        now = time.time()
        with self._lock:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO cache_entries
                    (cache_key, payload, tokens_in, tokens_out, cost_usd, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    key,
                    json.dumps(payload, sort_keys=True, default=str),
                    int(tokens_in),
                    int(tokens_out),
                    float(cost_usd),
                    now,
                ),
            )
        logger.info(
            "assistant cache: stored key=%s… tokens_in=%d tokens_out=%d cost=$%.4f",
            key[:12], tokens_in, tokens_out, cost_usd,
        )
        return CachedResponse(
            cache_key=key,
            payload=payload,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            created_at=now,
        )

    def clear(self) -> int:
        """Delete every entry. Returns the row count deleted.

        Intended for tests and the operator "wipe my cache" path; not
        called automatically.
        """
        with self._lock:
            cur = self._conn.execute("DELETE FROM cache_entries")
            return cur.rowcount or 0

    def keys(self) -> Iterable[str]:
        """All cache keys present. Useful for introspection / tests."""
        return (row[0] for row in self._conn.execute("SELECT cache_key FROM cache_entries"))

    def close(self) -> None:
        with self._lock:
            self._conn.close()


__all__ = ["CacheKeyInputs", "CachedResponse", "ResponseCache"]
