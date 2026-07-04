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
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Iterable

logger = logging.getLogger(__name__)

_TABLE = "instrument_names"
# On-demand misses are fetched CONCURRENTLY (bounded) so a page of uncached
# symbols resolves in ~one Polygon round-trip of latency instead of N. The
# whole ticker universe should be pre-warmed via backfill_all() so on-demand
# fetches are rare (brand-new tickers only).
_MAX_FETCH_PER_CALL = 200
_FETCH_CONCURRENCY = 12


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
    """READ path — ClickHouse only. Returns a name entry per requested symbol
    (empty strings for any not yet cached). This never touches the provider:
    the hot path must not depend on Polygon being reachable or rate-limited.

    Coverage is maintained out-of-band by ``warm``/``warm_stream_universe``
    (invoked on startup, on the nightly refresh, and when a symbol is newly
    added to a watchlist/stream) — see run_names_refresh_loop."""
    syms = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))
    if not syms:
        return {}

    try:
        known = _ch_get(syms)
    except Exception as exc:  # noqa: BLE001 — CH down: degrade to empty names
        logger.warning("instrument_names CH read failed: %s", exc)
        known = {}

    return {s: known.get(s, {"description": "", "exchange": "", "asset_type": ""}) for s in syms}


def warm(symbols: Iterable[str], *, refresh: bool = False) -> int:
    """WRITE path — the ONLY place that touches the provider. Fetches company
    names from Polygon (concurrently, bounded) and writes them to ClickHouse
    so the CH-only read path serves them instantly.

    Skips symbols already cached unless ``refresh=True`` (nightly re-fetch to
    catch renames). Negatives are cached so unknown symbols aren't re-fetched.
    Returns the number of symbols that ended up with a non-empty name.
    """
    syms = list(dict.fromkeys(s.strip().upper() for s in symbols if s and s.strip()))
    if not syms:
        return 0
    try:
        ensure_table()
        cached = {} if refresh else _ch_get(syms)
    except Exception as exc:  # noqa: BLE001 — CH down
        logger.warning("instrument_names warm CH read failed: %s", exc)
        cached = {}

    to_fetch = [s for s in syms if s not in cached]
    fetched: dict[str, dict] = {}
    if to_fetch:
        with ThreadPoolExecutor(max_workers=_FETCH_CONCURRENCY) as ex:
            recs = list(ex.map(_polygon_fetch, to_fetch))
        for sym, rec in zip(to_fetch, recs):
            fetched[sym] = rec or {"description": "", "exchange": "", "asset_type": ""}
        try:
            _ch_put({s: {**r, "source": "polygon"} for s, r in fetched.items()})
        except Exception as exc:  # noqa: BLE001 — cache write best-effort
            logger.warning("instrument_names warm CH write failed: %s", exc)

    named = sum(1 for v in {**cached, **fetched}.values() if v.get("description"))
    logger.info(
        "instrument_names warm: %d symbols (%d fetched, %d already cached) — %d named",
        len(syms), len(to_fetch), len(syms) - len(to_fetch), named,
    )
    return named


def _stream_universe_symbols() -> list[str]:
    try:
        from app.db.client import get_client

        return [r[0] for r in get_client().query(
            "SELECT DISTINCT symbol FROM stream_universe WHERE symbol NOT LIKE '/%'"
        ).result_rows]
    except Exception as exc:  # noqa: BLE001 — best effort
        logger.warning("instrument_names: could not read stream_universe: %s", exc)
        return []


def warm_stream_universe() -> int:
    """Fill any MISSING names for the live streaming universe (fast — skips
    already-cached symbols). Safe to call on startup / on universe change."""
    return warm(_stream_universe_symbols())


def refresh_names() -> dict:
    """Nightly job body — RE-FETCH names for the streaming universe from
    Polygon (catches renames + fills gaps). The only scheduled provider call.
    Returns a small summary for the job audit."""
    syms = _stream_universe_symbols()
    named = warm(syms, refresh=True)
    return {"symbols": len(syms), "named": named}


def _seconds_until_next_run(hour_utc: int, *, now: datetime | None = None) -> float:
    from datetime import timedelta

    now = now or datetime.now(timezone.utc)
    h = max(0, min(23, int(hour_utc)))
    target = now.replace(hour=h, minute=0, second=0, microsecond=0)
    if target <= now:
        target = target + timedelta(days=1)
    return max(1.0, (target - now).total_seconds())


async def run_names_refresh_loop() -> None:
    """Daily loop: sleep until the configured hour, then refresh_names().
    Keeps the CH name cache current without any provider calls on the read
    path. Registered + audited in main_api."""
    import asyncio

    from app.config import settings

    hour = int(getattr(settings, "instrument_names_refresh_run_hour_utc", 8))
    logger.info("instrument_names refresh: loop armed (run hour %02d:00 UTC)", hour)
    while True:
        try:
            await asyncio.sleep(_seconds_until_next_run(hour))
            from app.services.jobs.service import audit_run

            async with audit_run("instrument_names_refresh") as rec:
                rec.result = await asyncio.to_thread(refresh_names)
        except asyncio.CancelledError:
            logger.info("instrument_names refresh: loop cancelled")
            raise
        except Exception as e:  # noqa: BLE001 — keep the loop alive
            logger.exception("instrument_names refresh: loop error: %s", e)
            await asyncio.sleep(300)
