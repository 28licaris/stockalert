"""
Seed-universe CRUD over the ClickHouse `seed_universe` table.

The seed universe is the operator's explicit "permanently streaming"
set. See [docs/frontend_api_contracts.md §10.1] for the locked sticky-
universe model.

Side-effect contract (mutations):
  - ADD: writes the row + calls
    `WatchlistService.add_members("default", [symbol])` so the existing
    refcounted subscribe + backfill path actually fires. The CH row is
    the audit log; the streaming machinery still goes through watchlist.
  - REMOVE: marks the row inactive AND calls
    `WatchlistService.remove_members("default", [symbol])` to decrement
    the refcount. Symbols held by OTHER watchlists keep streaming
    (refcount > 0) — which matches the sticky-universe intent
    ("watchlist-remove doesn't strip universe; explicit seed-remove
    can — if no other watchlist holds it").

Bootstrap (first read with empty table):
  Populate from `SEED_SYMBOLS` (the curated 100 in
  app/data/seed_universe.py) ∪ active members of the `default`
  watchlist. One-time; subsequent reads return whatever the CH table
  holds.

Threading + caching:
  Stateless service. Underlying CH client manages its own pool.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from app.db.client import get_client

logger = logging.getLogger(__name__)


def _normalize_symbol(s: str) -> str:
    """Match WatchlistRepo's normalization: uppercase, strip whitespace.

    Lazy-imported so we can swap to a futures-aware normalizer later
    without a circular import.
    """
    from app.db import watchlist_repo

    return watchlist_repo.normalize_member_symbol(s)


def _ts(value: datetime | None) -> str:
    """ISO 8601 with Z suffix for naive ClickHouse datetimes."""
    if value is None:
        return ""
    if value.tzinfo is None:
        return value.isoformat() + "Z"
    return value.isoformat()


class SeedService:
    """CH-backed CRUD over the seed_universe table.

    Use `seed_service` (module-level singleton) for production calls;
    constructor + `from_settings()` exist for test injection.
    """

    DEFAULT_OWNER = "default-tenant"

    @classmethod
    def from_settings(cls) -> "SeedService":
        return cls()

    # ─────────────────────────────────────────────────────────────────
    # Read
    # ─────────────────────────────────────────────────────────────────

    def list_seed(self, *, owner_id: Optional[str] = None) -> list[dict]:
        """Active seed-universe entries for the given owner, oldest-first."""
        owner = owner_id or self.DEFAULT_OWNER
        client = get_client()
        rows = client.query(
            """
            SELECT symbol,
                   asset_type,
                   added_at,
                   added_by,
                   notes
            FROM seed_universe
            FINAL
            WHERE owner_id = {owner:String}
              AND is_active = 1
            ORDER BY added_at ASC, symbol ASC
            """,
            parameters={"owner": owner},
        )
        return [
            {
                "symbol": r[0],
                "asset_type": r[1] or "",
                "added_at": _ts(r[2]),
                "added_by": r[3] or "",
                "notes": r[4] or "",
            }
            for r in rows.result_rows
        ]

    # ─────────────────────────────────────────────────────────────────
    # Mutate
    # ─────────────────────────────────────────────────────────────────

    def add(
        self,
        symbol: str,
        *,
        owner_id: Optional[str] = None,
        added_by: str = "",
        asset_type: str = "",
        notes: str = "",
    ) -> dict:
        """Promote a single symbol into the seed universe.

        Idempotent: re-adding a symbol that's already active is a no-op
        (returns `changed=[]`). Re-adding a previously removed (inactive)
        symbol re-activates it and re-subscribes.

        Side-effect: calls watchlist_service.add_members("default", [symbol])
        so the existing refcounted subscribe + backfill machinery fires.
        """
        sym = _normalize_symbol(symbol)
        if not sym:
            raise ValueError(f"invalid symbol {symbol!r} after normalization")

        owner = owner_id or self.DEFAULT_OWNER
        client = get_client()

        # Was this symbol already active in seed?
        already_active = bool(
            client.query(
                """
                SELECT 1 FROM seed_universe FINAL
                WHERE owner_id = {owner:String}
                  AND symbol = {sym:String}
                  AND is_active = 1
                LIMIT 1
                """,
                parameters={"owner": owner, "sym": sym},
            ).result_rows
        )

        # ReplacingMergeTree upsert: bump version + write the row.
        version = int(datetime.now(timezone.utc).timestamp() * 1000)
        client.insert(
            "seed_universe",
            [[sym, owner, asset_type or "", datetime.now(timezone.utc), added_by, notes, 1, version]],
            column_names=[
                "symbol",
                "owner_id",
                "asset_type",
                "added_at",
                "added_by",
                "notes",
                "is_active",
                "version",
            ],
        )

        # Side-effect: subscribe stream + trigger backfill via the
        # existing refcount machinery. Lazy-imported to avoid a
        # circular at module load.
        try:
            from app.services.live.watchlist_service import (
                DEFAULT_WATCHLIST_NAME,
                watchlist_service,
            )

            watchlist_service.add_members(DEFAULT_WATCHLIST_NAME, [sym])
        except Exception as exc:  # noqa: BLE001 — boundary
            logger.warning(
                "seed.add(%s): CH row written but watchlist subscribe failed: %s",
                sym,
                exc,
            )

        items = self.list_seed(owner_id=owner)
        return {
            "operation": "add",
            "changed": [] if already_active else [sym],
            "items": items,
            "count": len(items),
        }

    def remove(
        self,
        symbol: str,
        *,
        owner_id: Optional[str] = None,
    ) -> dict:
        """Take a symbol OUT of the seed universe.

        Marks the row inactive AND removes the symbol from the default
        watchlist (decrementing refcount). Other watchlists holding the
        same symbol keep streaming — refcount logic handles it.
        """
        sym = _normalize_symbol(symbol)
        if not sym:
            raise ValueError(f"invalid symbol {symbol!r} after normalization")

        owner = owner_id or self.DEFAULT_OWNER
        client = get_client()

        was_active = bool(
            client.query(
                """
                SELECT 1 FROM seed_universe FINAL
                WHERE owner_id = {owner:String}
                  AND symbol = {sym:String}
                  AND is_active = 1
                LIMIT 1
                """,
                parameters={"owner": owner, "sym": sym},
            ).result_rows
        )

        if was_active:
            version = int(datetime.now(timezone.utc).timestamp() * 1000)
            client.insert(
                "seed_universe",
                [[sym, owner, "", datetime.now(timezone.utc), "", "", 0, version]],
                column_names=[
                    "symbol",
                    "owner_id",
                    "asset_type",
                    "added_at",
                    "added_by",
                    "notes",
                    "is_active",
                    "version",
                ],
            )

            # Side-effect: decrement refcount on the default watchlist.
            try:
                from app.services.live.watchlist_service import (
                    DEFAULT_WATCHLIST_NAME,
                    watchlist_service,
                )

                watchlist_service.remove_members(DEFAULT_WATCHLIST_NAME, [sym])
            except Exception as exc:  # noqa: BLE001 — boundary
                logger.warning(
                    "seed.remove(%s): CH row marked inactive but watchlist remove failed: %s",
                    sym,
                    exc,
                )

        items = self.list_seed(owner_id=owner)
        return {
            "operation": "remove",
            "changed": [sym] if was_active else [],
            "items": items,
            "count": len(items),
        }

    def import_bulk(
        self,
        symbols: list[str],
        *,
        owner_id: Optional[str] = None,
        added_by: str = "",
        notes: str = "",
    ) -> dict:
        """Bulk-add symbols. Idempotent."""
        owner = owner_id or self.DEFAULT_OWNER
        changed: list[str] = []
        for raw in symbols:
            sym = _normalize_symbol(raw)
            if not sym:
                continue
            result = self.add(
                sym,
                owner_id=owner,
                added_by=added_by,
                notes=notes,
            )
            changed.extend(result["changed"])

        items = self.list_seed(owner_id=owner)
        return {
            "operation": "import",
            "changed": changed,
            "items": items,
            "count": len(items),
        }

    # ─────────────────────────────────────────────────────────────────
    # Bootstrap
    # ─────────────────────────────────────────────────────────────────

    def is_empty(self, *, owner_id: Optional[str] = None) -> bool:
        owner = owner_id or self.DEFAULT_OWNER
        client = get_client()
        rows = client.query(
            """
            SELECT 1 FROM seed_universe FINAL
            WHERE owner_id = {owner:String} AND is_active = 1
            LIMIT 1
            """,
            parameters={"owner": owner},
        )
        return not rows.result_rows

    def bootstrap_if_empty(
        self, *, owner_id: Optional[str] = None
    ) -> tuple[bool, int]:
        """Populate from SEED_SYMBOLS ∪ default-watchlist members iff empty.

        Returns `(did_bootstrap, count)`. Idempotent — calling repeatedly
        is safe; subsequent calls return `(False, len)`.
        """
        owner = owner_id or self.DEFAULT_OWNER
        if not self.is_empty(owner_id=owner):
            return False, len(self.list_seed(owner_id=owner))

        # Curated 100 from app/data/seed_universe.py
        from app.data.seed_universe import SEED_SYMBOLS

        seed_pool: set[str] = {s for s in SEED_SYMBOLS if s}

        # Current default-watchlist members
        try:
            from app.services.live.watchlist_service import (
                DEFAULT_WATCHLIST_NAME,
                watchlist_service,
            )

            seed_pool.update(watchlist_service.list_members(DEFAULT_WATCHLIST_NAME))
        except Exception as exc:  # noqa: BLE001 — boundary
            logger.warning("bootstrap: could not read default watchlist: %s", exc)

        if not seed_pool:
            return False, 0

        # Bulk-insert WITHOUT triggering the per-symbol watchlist subscribe
        # side-effect — those symbols are ALREADY in the watchlist machinery
        # (either via the curated env or the existing default-watchlist).
        # Bootstrap is just shaping the audit log to match reality.
        rows = []
        now = datetime.now(timezone.utc)
        version = int(now.timestamp() * 1000)
        for sym in sorted(seed_pool):
            rows.append(
                [
                    sym,
                    owner,
                    "",  # asset_type unknown at bootstrap
                    now,
                    "bootstrap",
                    "imported from SEED_SYMBOLS + default watchlist",
                    1,
                    version,
                ]
            )
        client = get_client()
        client.insert(
            "seed_universe",
            rows,
            column_names=[
                "symbol",
                "owner_id",
                "asset_type",
                "added_at",
                "added_by",
                "notes",
                "is_active",
                "version",
            ],
        )
        logger.info("seed_universe: bootstrapped with %d symbols", len(rows))
        return True, len(rows)


# Module-level singleton — matches the WatchlistService pattern.
seed_service = SeedService()
