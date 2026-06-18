"""Resolve the futures monitoring universe (continuous roots).

Mirrors ``app.services.universe.resolve_universe_spec`` but for futures:
the canonical "active" set is the ``stocks.futures_universe`` ClickHouse
table (the same table the live stream subscribes from), with
``FUTURES_SEED_ROOTS`` as the cold-start / fallback list.
"""
from __future__ import annotations

import logging

from app.services.futures.schemas import FUTURES_SEED_ROOTS

logger = logging.getLogger(__name__)

DEFAULT_OWNER = "default-tenant"


def active_futures_roots(*, owner_id: str = DEFAULT_OWNER) -> list[str]:
    """Active continuous roots in ``futures_universe`` (``is_active=1``).

    Falls back to ``FUTURES_SEED_ROOTS`` if the table is empty or
    unreadable — a fresh deployment with no seeded rows still backfills
    the standard set rather than silently pulling nothing (NO silent
    failure: the fallback is logged).
    """
    try:
        from app.db.client import get_client

        rows = get_client().query(
            "SELECT symbol FROM futures_universe FINAL "
            "WHERE owner_id = {owner:String} AND is_active = 1 "
            "ORDER BY symbol",
            parameters={"owner": owner_id},
        )
        roots = [r[0] for r in rows.result_rows]
    except Exception as exc:  # noqa: BLE001 — boundary; CH optional for futures
        logger.warning(
            "active_futures_roots: futures_universe read failed (%s) — "
            "falling back to %d seed roots",
            exc, len(FUTURES_SEED_ROOTS),
        )
        return list(FUTURES_SEED_ROOTS)

    if not roots:
        logger.info(
            "active_futures_roots: futures_universe empty — using %d seed roots",
            len(FUTURES_SEED_ROOTS),
        )
        return list(FUTURES_SEED_ROOTS)
    return roots


def resolve_futures_spec(spec: str) -> list[str]:
    """Translate a config spec string → list of continuous roots.

    Spec strings:
      - "active"/"universe"/"dynamic"/""/None → futures_universe (∪ seed fallback)
      - "seed"                                → ``FUTURES_SEED_ROOTS`` (static)
      - "/ES,/NQ"                             → explicit CSV (leading '/' enforced)
    """
    s = (spec or "").strip().lower()
    if s in ("", "active", "universe", "dynamic"):
        return active_futures_roots()
    if s in ("seed", "seed-roots", "seed_roots"):
        return list(FUTURES_SEED_ROOTS)
    out: list[str] = []
    for tok in spec.split(","):
        t = tok.strip().upper()
        if t:
            out.append(t if t.startswith("/") else "/" + t)
    return out
