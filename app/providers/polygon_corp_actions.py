"""
Polygon corporate-actions REST client.

Wraps Polygon's two corp-action endpoints:
  - GET /v3/reference/splits
  - GET /v3/reference/dividends

Returns canonical `CorpAction` (Pydantic) — same shape silver
consumes. Pagination follows Polygon's `next_url` cursor; rate
limiting is a configurable polite-sleep between requests.

**Scope:**
- One-shot historical backfill (`since=2003-01-01`).
- Nightly incremental (`since=yesterday`).
- Idempotent at the silver layer (Iceberg `MERGE INTO` on
  `(symbol, ex_date, action_type)`).

**Not in scope here:**
- Iceberg writes — see `app/services/silver/corp_actions_ingest.py`.
- Stock-dividend handling beyond Polygon's payload — Polygon
  doesn't expose stock dividends as a distinct endpoint; they
  surface as `dividend_type: SC` in /dividends.
- Spinoffs — same caveat; Polygon labels them under /dividends
  with `dividend_type: SP`.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime, timezone
from typing import Any, AsyncIterator, Iterable, Optional

import aiohttp

from app.services.silver.schemas import CorpAction, CorpActionKind

logger = logging.getLogger(__name__)


_REST_BASE = "https://api.polygon.io"
_SPLITS_PATH = "/v3/reference/splits"
_DIVIDENDS_PATH = "/v3/reference/dividends"

# Polygon pages at 1000 per request — their max. We always request the max
# to minimize round trips on the full-history backfill.
_PAGE_LIMIT = 1000


# Polygon dividend_type values — empirically what /dividends returns.
#
# Each maps to a distinct CorpActionKind. CD, LT, ST were originally
# collapsed under "cash_dividend" but that produced duplicate-key
# upsert errors when a fund/ETF issued multiple distributions on the
# same ex_date. Keeping them separate also matches their actual
# semantic distinctness (different tax treatments + different
# triggers).
_DIVIDEND_TYPE_MAP: dict[str, CorpActionKind] = {
    "CD": "cash_dividend",      # ordinary cash dividend
    "LT": "lt_capital_gain",    # long-term capital-gains distribution
    "ST": "st_capital_gain",    # short-term capital-gains distribution
    "SC": "stock_dividend",     # stock dividend (paid in shares)
    "SP": "spinoff",            # spin-off distribution
}


class PolygonCorpActionsClient:
    """Thin async REST client for Polygon's corp-actions endpoints.

    Construct via `from_settings()` for production use; pass explicit
    `api_key` for tests.
    """

    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = _REST_BASE,
        sleep_between_requests_s: float = 0.2,
        timeout_s: float = 30.0,
    ) -> None:
        if not api_key:
            raise ValueError(
                "PolygonCorpActionsClient requires a non-empty api_key. "
                "Set POLYGON_API_KEY in .env or pass explicitly."
            )
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._sleep_s = sleep_between_requests_s
        self._timeout = aiohttp.ClientTimeout(total=timeout_s)

    @classmethod
    def from_settings(cls) -> "PolygonCorpActionsClient":
        """Build a client from app.config.settings."""
        from app.config import settings
        return cls(api_key=settings.polygon_api_key)

    # ─────────────────────────────────────────────────────────────────
    # Public iterators — one CorpAction at a time, paginated under the hood
    # ─────────────────────────────────────────────────────────────────

    async def iter_splits(
        self,
        *,
        since: Optional[date] = None,
        until: Optional[date] = None,
    ) -> AsyncIterator[CorpAction]:
        """Iterate Polygon `/v3/reference/splits` between dates.

        Both bounds are inclusive on `execution_date` (Polygon's name
        for the ex-date). If `since` is None, returns the full history
        Polygon has (back to ~1980 for major US equities).
        """
        params = self._date_window_params(since, until, date_field="execution_date")
        async for raw in self._iter_paginated(_SPLITS_PATH, params):
            yield self._split_to_corp_action(raw)

    async def iter_dividends(
        self,
        *,
        since: Optional[date] = None,
        until: Optional[date] = None,
    ) -> AsyncIterator[CorpAction]:
        """Iterate Polygon `/v3/reference/dividends` between dates.

        Bounds applied to `ex_dividend_date`. Returns ordinary cash
        dividends + stock dividends + spinoffs + special-distribution
        cash dividends, mapped to `CorpActionKind` per `_DIVIDEND_TYPE_MAP`.
        """
        params = self._date_window_params(since, until, date_field="ex_dividend_date")
        async for raw in self._iter_paginated(_DIVIDENDS_PATH, params):
            action = self._dividend_to_corp_action(raw)
            if action is not None:
                yield action

    async def collect_splits(
        self,
        *,
        since: Optional[date] = None,
        until: Optional[date] = None,
    ) -> list[CorpAction]:
        """Convenience: drain iter_splits into a list."""
        return [a async for a in self.iter_splits(since=since, until=until)]

    async def collect_dividends(
        self,
        *,
        since: Optional[date] = None,
        until: Optional[date] = None,
    ) -> list[CorpAction]:
        """Convenience: drain iter_dividends into a list."""
        return [a async for a in self.iter_dividends(since=since, until=until)]

    # ─────────────────────────────────────────────────────────────────
    # Internals
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _date_window_params(
        since: Optional[date],
        until: Optional[date],
        *,
        date_field: str,
    ) -> dict[str, Any]:
        """Build the gte/lte params Polygon uses for date filtering."""
        params: dict[str, Any] = {
            "limit": _PAGE_LIMIT,
            "order": "asc",
            "sort": date_field,
        }
        if since is not None:
            params[f"{date_field}.gte"] = since.isoformat()
        if until is not None:
            params[f"{date_field}.lte"] = until.isoformat()
        return params

    async def _iter_paginated(
        self,
        path: str,
        params: dict[str, Any],
    ) -> AsyncIterator[dict[str, Any]]:
        """Walk Polygon's cursor-paginated results.

        Polygon includes a `next_url` field in each response when more
        results are available. We follow it until exhausted, sleeping
        between requests to stay polite.
        """
        url = f"{self._base_url}{path}"
        first_page = True

        async with aiohttp.ClientSession(timeout=self._timeout) as session:
            while True:
                # First page: full params + apiKey. Subsequent pages:
                # follow `next_url` as-is + append apiKey (Polygon strips
                # it on the cursor URL).
                if first_page:
                    req_params = {**params, "apiKey": self._api_key}
                    req_url = url
                else:
                    req_params = {"apiKey": self._api_key}
                    # `url` is now the next_url; use as-is.
                    req_url = url

                async with session.get(req_url, params=req_params) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        raise RuntimeError(
                            f"Polygon corp-actions request failed: "
                            f"{resp.status} {text[:200]}"
                        )
                    payload = await resp.json()

                results = payload.get("results") or []
                for row in results:
                    yield row

                next_url = payload.get("next_url")
                if not next_url:
                    return

                url = next_url
                first_page = False
                await asyncio.sleep(self._sleep_s)

    # ─────────────────────────────────────────────────────────────────
    # Row → CorpAction mapping
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _split_to_corp_action(row: dict[str, Any]) -> CorpAction:
        """Map one Polygon /splits row to a CorpAction.

        Polygon's split shape:
            {
              "execution_date": "2020-08-31",
              "ticker": "AAPL",
              "split_from": 1,
              "split_to": 4,
              "id": "...",
            }
        Factor = split_to / split_from (4.0 for 4-for-1; 0.5 for 1-for-2 reverse).
        """
        ex_date = date.fromisoformat(row["execution_date"])
        symbol = (row.get("ticker") or "").upper()
        split_to = float(row.get("split_to") or 1)
        split_from = float(row.get("split_from") or 1) or 1.0
        factor = split_to / split_from
        return CorpAction(
            symbol=symbol,
            ex_date=ex_date,
            action_type="split",
            factor=factor,
            cash_amount=None,
            announced_at=None,
            source_provider="polygon",
        )

    @staticmethod
    def _dividend_to_corp_action(row: dict[str, Any]) -> Optional[CorpAction]:
        """Map one Polygon /dividends row to a CorpAction.

        Polygon's dividend shape:
            {
              "ex_dividend_date": "2020-08-07",
              "ticker": "AAPL",
              "cash_amount": 0.82,
              "declaration_date": "2020-07-30",
              "dividend_type": "CD",          # mapped via _DIVIDEND_TYPE_MAP
              "frequency": 4,                  # quarterly etc. (unused for now)
              "pay_date": "2020-08-13",
              "record_date": "2020-08-10",
              ...
            }
        Returns None if the dividend_type isn't recognised (rather than
        polluting silver with an unknown kind).
        """
        kind = _DIVIDEND_TYPE_MAP.get((row.get("dividend_type") or "").upper())
        if kind is None:
            logger.debug(
                "Skipping dividend with unknown dividend_type=%r ticker=%s",
                row.get("dividend_type"), row.get("ticker"),
            )
            return None

        ex_date_str = row.get("ex_dividend_date")
        if not ex_date_str:
            return None
        ex_date = date.fromisoformat(ex_date_str)
        symbol = (row.get("ticker") or "").upper()
        cash_amount = row.get("cash_amount")
        cash_amount_f = float(cash_amount) if cash_amount is not None else None

        announced_at: Optional[datetime] = None
        decl_str = row.get("declaration_date")
        if decl_str:
            try:
                announced_at = datetime.fromisoformat(decl_str).replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                announced_at = None

        return CorpAction(
            symbol=symbol,
            ex_date=ex_date,
            action_type=kind,
            factor=None,        # cash dividends + spinoffs don't have a factor
            cash_amount=cash_amount_f,
            announced_at=announced_at,
            source_provider="polygon",
        )
