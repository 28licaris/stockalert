"""
QuoteService — provider-quote abstraction (REST, not CH).

Wraps `app.config.get_market_quotes_provider()` and normalizes the
provider-specific quote shapes (Schwab uses `lastPrice`, Polygon uses
different keys, etc.) into the canonical `Quote` Pydantic model.

The fallback chain from `get_market_quotes_provider` is preserved —
if the active `DATA_PROVIDER` doesn't expose `get_quotes`, the
function falls back to Schwab. Same behavior the banner uses today.

Async at the boundary because provider HTTP calls are async.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Optional

from app.services.readers.schemas import Quote, QuotesResponse

logger = logging.getLogger(__name__)


# Schwab accepts long comma-separated `symbols=` lists, but very large
# batches sometimes fail closed (token / URL limits). Chunk + accumulate
# partial successes so one bad chunk doesn't drop the whole tape.
_DEFAULT_QUOTE_CHUNK_SIZE = 25


# Provider-quote field-name fallback chain. Different providers use
# different keys for the same concept (Schwab: `lastPrice`; Polygon
# tickers endpoint: `last_quote.p` / `day.c`). We try each candidate
# in order and take the first non-null numeric value.
_FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "last": ("lastPrice", "last", "regularMarketLastPrice", "mark", "closePrice"),
    "bid": ("bidPrice", "bid"),
    "ask": ("askPrice", "ask"),
    "open": ("openPrice", "open", "regularMarketOpen"),
    "high": ("highPrice", "high", "regularMarketDayHigh"),
    "low": ("lowPrice", "low", "regularMarketDayLow"),
    "close": ("closePrice", "previousClose", "regularMarketPreviousClose"),
    "volume": ("totalVolume", "volume", "regularMarketVolume"),
}


def _pick_numeric(payload: dict[str, Any], aliases: tuple[str, ...]) -> Optional[float]:
    """Return the first non-null numeric value found at any alias."""
    for key in aliases:
        v = payload.get(key)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if f != 0.0 or key in ("close", "volume"):  # accept 0 only for close + volume
            return f
    return None


def _normalize_quote(symbol: str, payload: dict[str, Any], provider: str) -> Quote:
    """
    Convert a provider's raw quote dict into the canonical `Quote`
    shape. Provider-specific quirks are absorbed here so consumers get
    one stable contract regardless of which provider answered.
    """
    quote_payload = payload.get("quote") if isinstance(payload.get("quote"), dict) else payload

    ts_raw = (
        quote_payload.get("quoteTime")
        or quote_payload.get("tradeTime")
        or quote_payload.get("updated")
    )
    ts: Optional[datetime] = None
    if isinstance(ts_raw, (int, float)):
        # Schwab quotes are epoch-ms.
        try:
            ts = datetime.fromtimestamp(ts_raw / 1000.0, tz=timezone.utc)
        except (OSError, ValueError, OverflowError):
            ts = None
    elif isinstance(ts_raw, str):
        try:
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        except ValueError:
            ts = None

    return Quote(
        symbol=symbol,
        last=_pick_numeric(quote_payload, _FIELD_ALIASES["last"]),
        bid=_pick_numeric(quote_payload, _FIELD_ALIASES["bid"]),
        ask=_pick_numeric(quote_payload, _FIELD_ALIASES["ask"]),
        open=_pick_numeric(quote_payload, _FIELD_ALIASES["open"]),
        high=_pick_numeric(quote_payload, _FIELD_ALIASES["high"]),
        low=_pick_numeric(quote_payload, _FIELD_ALIASES["low"]),
        close=_pick_numeric(quote_payload, _FIELD_ALIASES["close"]),
        volume=_pick_numeric(quote_payload, _FIELD_ALIASES["volume"]),
        timestamp=ts,
        provider=provider,
    )


class QuoteService:
    """
    Provider-quote abstraction.

    Construct directly with a provider for tests; use `from_settings()`
    for the production path, which picks up whichever provider the
    config layer resolves (with Schwab fallback if the primary lacks
    `get_quotes`).
    """

    def __init__(self, provider: Any) -> None:
        self._provider = provider
        self._provider_name = self._derive_provider_name(provider)

    @classmethod
    def from_settings(cls) -> "QuoteService":
        from app.config import get_market_quotes_provider

        return cls(get_market_quotes_provider())

    @staticmethod
    def _derive_provider_name(provider: Any) -> str:
        if provider is None:
            return "none"
        cls_name = type(provider).__name__
        return cls_name.replace("Provider", "").lower() or "unknown"

    async def get_quote(self, symbol: str) -> Optional[Quote]:
        """
        Return the current quote for `symbol`, or `None` if the
        provider couldn't resolve it (invalid symbol, transient error,
        etc.). Logs warnings but does not raise on provider-level
        failures — the route layer can decide whether `None` should
        become a 404 or 503.
        """
        response = await self.get_quotes([symbol])
        return response.quotes.get(symbol)

    async def get_quotes(
        self,
        symbols: list[str],
        *,
        chunk_size: int = _DEFAULT_QUOTE_CHUNK_SIZE,
    ) -> QuotesResponse:
        """
        Return current quotes for the requested symbols. Symbols the
        provider couldn't resolve land in `invalid_symbols`.

        Chunked: large batches are split into `chunk_size`-sized
        requests so one bad chunk (network blip, URL-length limit,
        one symbol the provider chokes on) doesn't drop the entire
        tape. Per-chunk failures log a warning and continue.

        Empty input -> empty response (no provider call).
        """
        if not symbols:
            return QuotesResponse(quotes={}, count=0, invalid_symbols=[])

        getter = getattr(self._provider, "get_quotes", None)
        if getter is None:
            logger.warning(
                "QuoteService: provider %r has no get_quotes — returning empty",
                self._provider_name,
            )
            return QuotesResponse(quotes={}, count=0, invalid_symbols=list(symbols))

        merged: dict[str, dict[str, Any]] = {}
        invalid_acc: list[str] = []
        for i in range(0, len(symbols), chunk_size):
            chunk = symbols[i : i + chunk_size]
            try:
                part = await getter(chunk)
            except Exception as exc:  # noqa: BLE001 — boundary; partial-success semantics
                logger.warning(
                    "QuoteService chunk %d-%d (%d syms) failed: %s",
                    i, i + len(chunk), len(chunk), exc,
                )
                continue
            if not isinstance(part, dict):
                continue
            err = part.get("errors")
            if isinstance(err, dict):
                invalid_acc.extend(err.get("invalidSymbols") or [])
            for k, v in part.items():
                if k == "errors" or not isinstance(v, dict):
                    continue
                merged[k] = v

        quotes: dict[str, Quote] = {
            sym: _normalize_quote(sym, payload, self._provider_name)
            for sym, payload in merged.items()
        }
        return QuotesResponse(
            quotes=quotes,
            count=len(quotes),
            invalid_symbols=invalid_acc,
        )
