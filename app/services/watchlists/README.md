# watchlists — per-user watchlists with pretend positions

Per-user watchlists stored in the identity PostgreSQL (NOT ClickHouse —
user-owned state lives with users). Each member row carries a pretend
position: `quantity` (default `WATCHLIST_DEFAULT_QTY`, 100) and
`entry_price` stamped from the latest live price at add time. Returns
are computed at READ time (lean rule: no derived columns stored);
missing entry prices are backfilled at first sighting.

Layout: `models.py` (SQLAlchemy on IdentityBase), `schemas.py` (public
Pydantic contracts), `repository.py` (sessionmaker CRUD, all queries
scoped by user_id), `service.py` (price stamping, stream-universe join
per the tracked-instruments rule, read-time P&L).

API: `app/api/routes_my_watchlists.py` (`/api/v1/my/watchlists`),
gated by `get_principal_or_dev` — real Principal when AUTH_ENABLED,
a fixed local dev principal otherwise (identity DB still required).
Migration: `migrations/versions/20260703_05_watchlists.py`.
