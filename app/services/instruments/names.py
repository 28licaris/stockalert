"""Durable company-name resolver.

The live provider (Schwab) doesn't reliably resolve instrument descriptions
in every deployment, which left customer-facing surfaces (watchlists, stream
rows) showing bare tickers. This module keeps a durable ClickHouse table
``instrument_names`` as the source of truth and lazily fills misses from
Polygon's ``/v3/reference/tickers`` reference endpoint (a different
entitlement than the flat-files), caching every result — including negatives
— so a given symbol hits Polygon at most once.

Public API:
  - ``resolve_names(symbols) -> {SYMBOL: {"description", "exchange", "asset_type"}}``
    Always returns an entry for every requested symbol (empty strings if
    still unresolved). Safe to call from an async route via ``asyncio.to_thread``.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Iterable

logger = logging.getLogger(__name__)

_TABLE = "instrument_names"
# Cap Polygon fetches per call so a large cold lookup can't burst the API.
# Uncached-and-uncapped symbols simply come back empty this round and get
# filled on a later call (or by a bulk backfill).
_MAX_FETCH_PER_CALL = 50


def ensure_table() -> None:
    from app.db.client import get_client

    get_client().command(
        """
        CREATE TABLE IF NOT EXISTS instrument_names (
            symbol       LowCardinality(String),
            name         String DEFAULT '',
            exchange     String DEFAULT '',
            asset_type   LowCardinality(String) DEFAULT '',
            source       LowCardinality(String) DEFAULT '',
            updated_at   DateTime64(3, 'UTC'),
            row_version  UInt64
        )
        ENGINE = ReplacingMergeTree(row_version)
        ORDER BY symbol
        SETTINGS index_granularity = 8192
        """
    )


def _ch_get(symbols: list[str]) -> dict[str, dict]:
    from app.db.client import get_client

    rows = get_client().query(
        "SELECT symbol, name, exchange, asset_type FROM instrument_names FINAL "
        "WHERE symbol IN {syms:Array(String)}",
        parameters={"syms": symbols},
    ).result_rows
    return {
        r[0]: {"description": r[1] or "", "exchange": r[2] or "", "asset_type": r[3] or ""}
        for r in rows
    }


def _ch_put(records: dict[str, dict]) -> None:
    if not records:
        return
    from app.db.client import get_client

    now = datetime.now(timezone.utc)
    ver = int(now.timestamp() * 1000)
    rows = [
        [
            sym,
            rec.get("description", ""),
            rec.get("exchange", ""),
            rec.get("asset_type", ""),
            rec.get("source", "polygon"),
            now,
            ver,
        ]
        for sym, rec in records.items()
    ]
    get_client().insert(
        _TABLE,
        rows,
        column_names=["symbol", "name", "exchange", "asset_type", "source", "updated_at", "row_version"],
    )


def _polygon_fetch(symbol: str) -> dict | None:
    """Return {description, exchange, asset_type} from Polygon reference, or
    None on any failure. Only the `name` field is required to be useful."""
    from app.config import settings

    key = (settings.polygon_api_key or "").strip()
    if not key:
        return None
    try:
        import httpx

        r = httpx.get(
            "https://api.polygon.io/v3/reference/tickers",
            params={"ticker": symbol, "apiKey": key},
            timeout=10.0,
        )
        if r.status_code != 200:
            return None
        results = r.json().get("results") or []
        it = results[0] if isinstance(results, list) and results else results
        if not isinstance(it, dict):
            return None
        return {
            "description": it.get("name") or "",
            "exchange": it.get("primary_exchange") or "",
            "asset_type": it.get("type") or "",
        }
    except Exception as exc:  # noqa: BLE001 — names are best-effort
        logger.warning("polygon name fetch failed for %s: %s", symbol, exc)
        return None


def resolve_names(symbols: Iterable[str]) -> dict[str, dict]:
    """Resolve company names for `symbols`. CH-first; fills misses from
    Polygon and caches them (including negatives). Always returns an entry
    per requested symbol."""
    syms = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))
    if not syms:
        return {}

    try:
        ensure_table()
        known = _ch_get(syms)
    except Exception as exc:  # noqa: BLE001 — CH down: degrade to empty names
        logger.warning("instrument_names CH read failed: %s", exc)
        known = {}

    missing = [s for s in syms if s not in known]
    fetched: dict[str, dict] = {}
    for sym in missing[:_MAX_FETCH_PER_CALL]:
        rec = _polygon_fetch(sym)
        # Cache negatives too (empty description) so we don't re-hit Polygon.
        fetched[sym] = rec or {"description": "", "exchange": "", "asset_type": ""}
    if len(missing) > _MAX_FETCH_PER_CALL:
        logger.info(
            "resolve_names: capped Polygon fetches at %d (%d symbols uncached this round)",
            _MAX_FETCH_PER_CALL, len(missing) - _MAX_FETCH_PER_CALL,
        )

    try:
        _ch_put({s: {**r, "source": "polygon"} for s, r in fetched.items()})
    except Exception as exc:  # noqa: BLE001 — cache write is best-effort
        logger.warning("instrument_names CH write failed: %s", exc)

    out = {**known, **fetched}
    for s in syms:  # guarantee an entry for every requested symbol
        out.setdefault(s, {"description": "", "exchange": "", "asset_type": ""})
    return out
