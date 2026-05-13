"""
Polygon (Massive) data provider.

Implements DataProvider for Polygon / Massive (https://massive.com):

- REST historical bars via `massive.RESTClient.list_aggs` (sync, wrapped in
  ``asyncio.to_thread`` so we never block the event loop).
- Live WebSocket streaming via `massive.WebSocketClient`. The websocket runs in
  a dedicated daemon thread with its own event loop; each parsed `EquityAgg`
  event is converted into the SimpleNamespace bar shape the rest of the app
  expects and pushed back to the *main* loop via
  ``asyncio.run_coroutine_threadsafe``. This mirrors the pattern used by
  SchwabProvider / AlpacaProvider so callers don't need to know which provider
  is producing the stream.
"""
from __future__ import annotations

import asyncio
import logging
import threading
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Optional, Tuple

import pandas as pd

from app.providers.base import DataProvider

logger = logging.getLogger(__name__)


# Polygon WebSocket event for 1-minute aggregate bars. Subscriptions are sent
# as "AM.<TICKER>" (or "AM.*" for the whole market on entitlement levels that
# permit it).
WS_EVENT_MINUTE_AGG = "AM"
WS_SUB_PREFIX = "AM."


def _timeframe_to_polygon(timeframe: str) -> Tuple[int, str]:
    """
    Translate the StockAlert timeframe string to a Polygon (multiplier, timespan).

    Mirrors the cases SchwabProvider.historical_df accepts so callers can use
    the same timeframe vocabulary regardless of provider.
    """
    tf = (timeframe or "").lower().strip()
    if tf in ("1min", "1m"):
        return 1, "minute"
    if tf in ("5min", "5m"):
        return 5, "minute"
    if tf in ("15min", "15m"):
        return 15, "minute"
    if tf in ("30min", "30m"):
        return 30, "minute"
    if tf in ("1h", "60min", "1hour", "hour", "hourly"):
        return 1, "hour"
    if tf in ("1d", "1day", "day", "daily"):
        return 1, "day"
    if tf in ("1w", "1week", "week", "weekly"):
        return 1, "week"
    if tf in ("1mo", "1month", "month", "monthly"):
        return 1, "month"
    # Unknown values fall back to 1-minute, matching SchwabProvider's behavior.
    return 1, "minute"


class PolygonProvider(DataProvider):
    """
    Polygon / Massive data provider — REST history + 1-minute WebSocket stream.
    """

    # Polygon's gainers/losers endpoint is market-wide (all US stocks), not
    # per-index like Schwab. Surface this so the /api/movers route can skip its
    # multi-index fan-out and call ``get_movers`` exactly once.
    MOVERS_MARKET_WIDE: bool = True

    def __init__(
        self,
        api_key: str,
        *,
        feed: str = "socket.polygon.io",
        market: str = "stocks",
        secure_ws: bool = True,
        max_reconnects: int = 10,
        connect_timeout: float = 10.0,
        read_timeout: float = 30.0,
        retries: int = 3,
    ) -> None:
        self._api_key = api_key

        # REST settings
        self._connect_timeout = connect_timeout
        self._read_timeout = read_timeout
        self._retries = retries
        self._rest = None  # built lazily; see _rest_client()

        # WebSocket settings
        self._ws_feed = feed
        self._ws_market = market
        self._ws_secure = secure_ws
        self._ws_max_reconnects = max_reconnects
        self._ws = None  # WebSocketClient, lazy

        # Streaming runtime state
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._bar_callback: Optional[Callable] = None
        self._subscribed_tickers: list[str] = []
        self._streamer_thread: Optional[threading.Thread] = None
        self._streamer_loop: Optional[asyncio.AbstractEventLoop] = None
        self._streamer_started = False

        # Reference-data cache. Polygon's snapshot endpoints (used by the
        # market tape and top-movers route) deliberately omit the company
        # name, so we lazily fan out to ``/v3/reference/tickers/{ticker}`` and
        # remember the result process-wide. ``_ticker_unknown_names`` tracks
        # symbols that returned no metadata so we don't retry them every tick.
        self._ticker_name_cache: dict[str, str] = {}
        self._ticker_unknown_names: set[str] = set()
        # Bound the concurrent ticker-details fetches so a fresh movers refresh
        # (~40 tickers) doesn't fan out 40 simultaneous HTTPS handshakes.
        self._ticker_lookup_concurrency = 8

    # ---------- REST ----------

    def _rest_client(self):
        """
        Build (and cache) the synchronous massive REST client on first use.
        Importing here keeps module import cheap and lets tests patch the
        constructor before any call.
        """
        if self._rest is None:
            from massive import RESTClient
            self._rest = RESTClient(
                api_key=self._api_key,
                connect_timeout=self._connect_timeout,
                read_timeout=self._read_timeout,
                retries=self._retries,
            )
        return self._rest

    async def historical_df(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1Min",
    ) -> pd.DataFrame:
        """
        Fetch historical aggregate bars for ``symbol`` over ``[start, end]``.

        Returns a DataFrame indexed by UTC timestamps with columns
        ``open/high/low/close/volume/vwap/trade_count``. Returns an empty
        DataFrame on any error (matches SchwabProvider and AlpacaProvider
        contracts so callers can stay provider-agnostic).
        """
        multiplier, timespan = _timeframe_to_polygon(timeframe)
        ticker = (symbol or "").upper().strip()
        if not ticker:
            return pd.DataFrame()

        start_utc = start if start.tzinfo else start.replace(tzinfo=timezone.utc)
        end_utc = end if end.tzinfo else end.replace(tzinfo=timezone.utc)

        try:
            aggs = await asyncio.to_thread(
                self._fetch_aggs_sync,
                ticker, multiplier, timespan, start_utc, end_utc,
            )
        except Exception as e:
            logger.error(
                "Polygon historical_df failed for %s (%s): %s",
                ticker, timeframe, e,
            )
            return pd.DataFrame()

        if not aggs:
            return pd.DataFrame()

        rows: list[dict] = []
        for a in aggs:
            ts_ms = getattr(a, "timestamp", None)
            if ts_ms is None:
                continue
            ts = datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc)
            rows.append({
                "timestamp": ts,
                "open": float(a.open or 0),
                "high": float(a.high or 0),
                "low": float(a.low or 0),
                "close": float(a.close or 0),
                "volume": float(a.volume or 0),
                "vwap": float(getattr(a, "vwap", 0) or 0),
                "trade_count": int(getattr(a, "transactions", 0) or 0),
            })

        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        return df

    def _fetch_aggs_sync(
        self,
        ticker: str,
        multiplier: int,
        timespan: str,
        start: datetime,
        end: datetime,
    ) -> list:
        """
        Synchronous Polygon `list_aggs` call materialised to a list so the
        result is safe to hand back across the asyncio.to_thread boundary.
        """
        client = self._rest_client()
        return list(client.list_aggs(
            ticker=ticker,
            multiplier=multiplier,
            timespan=timespan,
            from_=start,
            to=end,
            adjusted=True,
            sort="asc",
            limit=50000,
        ))

    # ---------- Snapshots: quotes (market tape) + movers ----------

    @staticmethod
    def _translate_symbol_for_polygon(symbol: str) -> tuple[str, str]:
        """
        Map a caller-supplied symbol to (polygon_ticker, asset_class).

        The dashboard tape uses Schwab-flavored symbols (``$SPX`` for indices,
        ``/ESM26`` for futures, plain ``SPY`` for equities). Polygon uses a
        different namespace (``I:SPX``, ``X:BTCUSD``, etc.) so callers stay
        portable across providers if we translate here.

        Returns ``("", "unsupported")`` for symbols we can't route to Polygon
        snapshots (currently: futures), so the caller can record them as
        invalid without making a doomed API call.
        """
        s = (symbol or "").strip()
        if not s:
            return "", "unsupported"
        up = s.upper()
        if up.startswith("I:"):
            return up, "indices"
        if up.startswith("$"):
            # Schwab-style index ticker: "$SPX" -> Polygon "I:SPX".
            return f"I:{up[1:]}", "indices"
        if up.startswith("O:"):
            return up, "options"
        if up.startswith("X:") or up.startswith("C:"):
            return up, "fx_crypto"
        if up.startswith("/"):
            # Futures (e.g. "/ESM26"). Polygon has a separate Futures product
            # with a different SDK surface; for now we mark these unsupported.
            return "", "unsupported"
        return up, "stocks"

    def _stock_snapshot_block(self, snap: Any, fallback_symbol: str) -> dict:
        """
        Convert a Polygon ``TickerSnapshot`` (stocks) into the Schwab-shaped
        quote block the market-banner route already knows how to parse.

        Schwab returns
        ``{ "SPY": { "assetMainType": "EQUITY", "quote": {...}, "reference": {...} } }``
        We populate just enough of that shape (``lastPrice``, ``closePrice``,
        ``netChange``, ``netPercentChange``) for ``_extract_row`` to succeed.
        """
        last = None
        last_trade = getattr(snap, "last_trade", None)
        if last_trade is not None:
            last = getattr(last_trade, "price", None)
        if last is None:
            day = getattr(snap, "day", None)
            if day is not None:
                last = getattr(day, "close", None)

        prev_day = getattr(snap, "prev_day", None)
        prev_close = getattr(prev_day, "close", None) if prev_day is not None else None

        net_change = getattr(snap, "todays_change", None)
        pct_change = getattr(snap, "todays_change_percent", None)

        # Polygon's ``todays_change`` is occasionally null even when last/prev
        # are populated (e.g. very early premarket). Recompute defensively so
        # the dashboard never shows a blank "Δ" column.
        if net_change is None and last is not None and prev_close not in (None, 0):
            try:
                net_change = float(last) - float(prev_close)
            except (TypeError, ValueError):
                net_change = None
        if pct_change is None and net_change is not None and prev_close not in (None, 0):
            try:
                pct_change = (float(net_change) / float(prev_close)) * 100.0
            except (TypeError, ValueError, ZeroDivisionError):
                pct_change = None

        ticker = getattr(snap, "ticker", None) or fallback_symbol

        return {
            "assetMainType": "EQUITY",
            "symbol": ticker,
            "quote": {
                "lastPrice": float(last) if last is not None else None,
                "closePrice": float(prev_close) if prev_close is not None else None,
                "netChange": float(net_change) if net_change is not None else None,
                "netPercentChange": float(pct_change) if pct_change is not None else None,
            },
            "reference": {"description": ""},
        }

    @staticmethod
    def _index_snapshot_block(snap: Any, fallback_symbol: str) -> dict:
        """
        Convert a Polygon ``IndicesSnapshot`` into the Schwab-shaped quote
        block. Polygon's index snapshot stores OHLC+change in a ``session``
        sub-object rather than at the top level.
        """
        session = getattr(snap, "session", None)
        value = getattr(snap, "value", None)
        last = value
        prev_close = getattr(session, "previous_close", None) if session is not None else None
        net_change = getattr(session, "change", None) if session is not None else None
        pct = getattr(session, "change_percent", None) if session is not None else None

        if net_change is None and last is not None and prev_close not in (None, 0):
            try:
                net_change = float(last) - float(prev_close)
            except (TypeError, ValueError):
                net_change = None
        if pct is None and net_change is not None and prev_close not in (None, 0):
            try:
                pct = (float(net_change) / float(prev_close)) * 100.0
            except (TypeError, ValueError, ZeroDivisionError):
                pct = None

        ticker = getattr(snap, "ticker", None) or fallback_symbol
        desc = getattr(snap, "name", None) or ""

        return {
            "assetMainType": "INDEX",
            "symbol": ticker,
            "quote": {
                "lastPrice": float(last) if last is not None else None,
                "closePrice": float(prev_close) if prev_close is not None else None,
                "netChange": float(net_change) if net_change is not None else None,
                "netPercentChange": float(pct) if pct is not None else None,
            },
            "reference": {"description": desc},
        }

    def _fetch_stock_snapshots_sync(self, tickers: list[str]) -> list:
        client = self._rest_client()
        result = client.get_snapshot_all(market_type="stocks", tickers=tickers)
        # ``get_snapshot_all`` may return a list, generator, or a single
        # TickerSnapshot. Materialise to a list so the caller can iterate
        # safely after the to_thread boundary.
        if result is None:
            return []
        if isinstance(result, list):
            return result
        try:
            return list(result)
        except TypeError:
            return [result]

    def _fetch_index_snapshots_sync(self, tickers: list[str]) -> list:
        client = self._rest_client()
        result = client.get_snapshot_indices(ticker_any_of=tickers)
        if result is None:
            return []
        if isinstance(result, list):
            return result
        try:
            return list(result)
        except TypeError:
            return [result]

    async def get_quotes(self, symbols: list[str], **_kwargs: Any) -> dict:
        """
        Symbol-keyed quote snapshot, Schwab-compatible shape.

        Drives the dashboard market banner (``GET /api/market/banner``). The
        existing route was written against Schwab's ``QuoteResponse``; we mimic
        that shape so the route is provider-agnostic.

        Buckets the requested symbols by Polygon asset class, fans out to the
        appropriate snapshot endpoint, then merges. Anything we can't route to
        Polygon (currently: futures via ``/...``) is collected into
        ``errors.invalidSymbols`` so the dashboard can grey it out instead of
        silently dropping it.
        """
        if not symbols:
            return {}

        stocks: dict[str, str] = {}
        indices: dict[str, str] = {}
        invalid: list[str] = []
        for raw in symbols:
            polygon_tkr, klass = self._translate_symbol_for_polygon(raw)
            if not polygon_tkr or klass == "unsupported":
                if raw:
                    invalid.append(raw)
                continue
            if klass == "stocks":
                stocks[polygon_tkr] = raw
            elif klass == "indices":
                indices[polygon_tkr] = raw
            else:
                # options / fx / crypto aren't part of the banner; skip
                invalid.append(raw)

        merged: dict[str, Any] = {}

        if stocks:
            try:
                stock_snaps = await asyncio.to_thread(
                    self._fetch_stock_snapshots_sync, list(stocks.keys())
                )
                seen = set()
                for snap in stock_snaps:
                    ticker = (getattr(snap, "ticker", None) or "").upper()
                    if not ticker or ticker not in stocks:
                        continue
                    original = stocks[ticker]
                    merged[original] = self._stock_snapshot_block(snap, original)
                    seen.add(ticker)
                for polygon_tkr, original in stocks.items():
                    if polygon_tkr not in seen:
                        invalid.append(original)
            except Exception as e:
                # Plan-tier errors (e.g. "NOT_AUTHORIZED") come back as
                # massive.exceptions.BadResponse — log them at WARNING with a
                # hint so users can tell apart "wrong key" from "wrong plan".
                logger.warning(
                    "Polygon get_quotes (stocks) failed (%s): %s",
                    type(e).__name__, e,
                )
                invalid.extend(stocks.values())

        if indices:
            try:
                idx_snaps = await asyncio.to_thread(
                    self._fetch_index_snapshots_sync, list(indices.keys())
                )
                seen = set()
                for snap in idx_snaps:
                    ticker = (getattr(snap, "ticker", None) or "").upper()
                    if not ticker or ticker not in indices:
                        continue
                    original = indices[ticker]
                    merged[original] = self._index_snapshot_block(snap, original)
                    seen.add(ticker)
                for polygon_tkr, original in indices.items():
                    if polygon_tkr not in seen:
                        invalid.append(original)
            except Exception as e:
                msg = str(e)
                if "NOT_AUTHORIZED" in msg or "entitled" in msg:
                    logger.warning(
                        "Polygon get_quotes (indices) skipped — current plan is "
                        "not entitled to the Indices Snapshot endpoint. Tickers "
                        "marked invalid: %s. Tip: substitute ETF proxies (e.g. "
                        "SPY/QQQ/DIA/IWM/VIXY) in MARKET_BANNER_SYMBOLS, or "
                        "upgrade the Polygon plan.",
                        list(indices.values()),
                    )
                else:
                    logger.warning(
                        "Polygon get_quotes (indices) failed (%s): %s",
                        type(e).__name__, e,
                    )
                invalid.extend(indices.values())

        if invalid:
            merged["errors"] = {"invalidSymbols": invalid}

        # Best-effort: enrich each populated block with the company name so the
        # dashboard's "description" column isn't always blank. Failures are
        # silent — the route already tolerates missing descriptions.
        try:
            keys_to_lookup = [k for k in merged.keys() if k != "errors"]
            tickers_to_resolve = [
                merged[k].get("symbol") or self._translate_symbol_for_polygon(k)[0]
                for k in keys_to_lookup
            ]
            names = await self._lookup_ticker_names(
                [t for t in tickers_to_resolve if t]
            )
            for k, polygon_tkr in zip(keys_to_lookup, tickers_to_resolve):
                if not polygon_tkr:
                    continue
                name = names.get(polygon_tkr)
                if name and not merged[k].get("reference", {}).get("description"):
                    merged[k].setdefault("reference", {})["description"] = name
        except Exception as e:
            logger.debug("Polygon get_quotes: name enrichment failed: %s", e)

        return merged

    # ---------- Ticker reference cache (company names) ----------

    def _fetch_ticker_name_sync(self, ticker: str) -> Optional[str]:
        """
        Synchronously look up a single ticker's company name via Polygon's
        Reference API. Returns ``None`` for any failure (unknown ticker, plan
        restriction, network); callers cache the absence so we don't retry.
        """
        try:
            client = self._rest_client()
            details = client.get_ticker_details(ticker=ticker)
        except Exception as e:
            logger.debug("Polygon ticker details %s failed: %s", ticker, e)
            return None
        return getattr(details, "name", None) if details is not None else None

    async def _lookup_ticker_names(self, tickers: list[str]) -> dict[str, str]:
        """
        Batch-resolve company names for a list of Polygon tickers.

        Returns a ``{ticker: name}`` map containing only the tickers we have a
        name for (callers should treat absence as "no description"). Results
        are cached in ``self._ticker_name_cache`` for the lifetime of the
        process; tickers that resolved to nothing land in
        ``self._ticker_unknown_names`` to avoid hammering the API on every
        subsequent movers refresh.
        """
        if not tickers:
            return {}
        unique = list(dict.fromkeys(t for t in tickers if t))
        unknown = [
            t for t in unique
            if t not in self._ticker_name_cache and t not in self._ticker_unknown_names
        ]
        if unknown:
            sem = asyncio.Semaphore(self._ticker_lookup_concurrency)

            async def fetch_one(t: str) -> None:
                async with sem:
                    name = await asyncio.to_thread(self._fetch_ticker_name_sync, t)
                if name:
                    self._ticker_name_cache[t] = name
                else:
                    self._ticker_unknown_names.add(t)

            await asyncio.gather(
                *(fetch_one(t) for t in unknown),
                return_exceptions=True,
            )
        return {t: self._ticker_name_cache[t] for t in unique if t in self._ticker_name_cache}

    # ---------- Instrument search (autocomplete) ----------

    # Polygon's reference data uses its own taxonomy in the ``type`` field
    # (e.g. ``CS`` = Common Stock, ``ADRC`` = American Depositary Receipt).
    # Map the common cases to the same vocabulary SchwabProvider returns so
    # the dashboard's add-to-watchlist autocomplete reads identically across
    # providers. Anything not in the map passes through unchanged (uppercased).
    _POLYGON_TYPE_TO_ASSET_TYPE: dict[str, str] = {
        "CS":      "EQUITY",       # common stock
        "PFD":     "EQUITY",       # preferred stock
        "ADRC":    "EQUITY",       # ADR common
        "ADRP":    "EQUITY",       # ADR preferred
        "ADRR":    "EQUITY",       # ADR rights
        "ETF":     "ETF",
        "ETN":     "ETF",          # exchange-traded note
        "ETV":     "ETF",          # exchange-traded vehicle
        "ETS":     "ETF",          # single-security ETF
        "FUND":    "MUTUAL_FUND",
        "SP":      "INDEX",        # Polygon tags some indices as "SP"
        "WARRANT": "WARRANT",
        "RIGHT":   "RIGHT",
        "UNIT":    "UNIT",
        "BOND":    "BOND",
        "BASKET":  "BASKET",
    }

    # Polygon's ``primary_exchange`` is an ISO 10383 MIC code; the dashboard
    # autocomplete renders these next to the symbol, so translate the most
    # common US venues to short friendly names users actually recognise.
    # Anything not in this map falls through to the raw MIC.
    _POLYGON_EXCHANGE_MIC_TO_NAME: dict[str, str] = {
        "XNAS": "NASDAQ",
        "XNYS": "NYSE",
        "ARCX": "NYSE ARCA",
        "BATS": "BATS",
        "XASE": "NYSE AMEX",
        "IEXG": "IEX",
        "OTCM": "OTC",
    }

    @classmethod
    def _normalize_search_ticker(cls, t: Any) -> Optional[dict]:
        """
        Convert a Polygon ``Ticker`` reference object into the provider-
        agnostic shape ``/api/instruments/search`` expects:
        ``{symbol, description, exchange, asset_type}``. Returns None for
        records missing a usable symbol so callers can skip them silently.
        """
        symbol = (getattr(t, "ticker", None) or "").upper().strip()
        if not symbol:
            return None
        raw_type = (getattr(t, "type", None) or "").upper()
        asset_type = cls._POLYGON_TYPE_TO_ASSET_TYPE.get(raw_type, raw_type)
        mic = (getattr(t, "primary_exchange", None) or "").upper()
        exchange = cls._POLYGON_EXCHANGE_MIC_TO_NAME.get(mic, mic)
        return {
            "symbol": symbol,
            "description": getattr(t, "name", None) or "",
            "exchange": exchange,
            "asset_type": asset_type,
        }

    # Asset-type bias used to rerank Polygon's `search` results. The Polygon
    # endpoint is already relevance-ordered server-side, but a query like
    # "apple" can still surface preferred shares or warrants above the common
    # stock; these weights nudge retail-friendly types up. Mirrors Schwab's
    # behavior so the autocomplete dropdown feels consistent across providers.
    _SEARCH_TYPE_BIAS = {
        "EQUITY":      30,
        "ETF":         25,
        "INDEX":       20,
        "MUTUAL_FUND":  5,
        "WARRANT":      0,
        "RIGHT":        0,
        "UNIT":         0,
        "BOND":         0,
    }

    @classmethod
    def _score_search_result(cls, inst: dict, q_upper: str, server_rank: int) -> int:
        """
        Rerank a normalized search result. Higher = more relevant.

        We weight Polygon's server-side relevance ranking heavily (since
        Polygon already considers ticker + name fuzziness), then layer on:
          - exact / prefix ticker matches (huge boost),
          - asset-type bias toward EQUITY / ETF,
          - a small length penalty so AAPL outranks AAPLW on ties.
        """
        sym = inst.get("symbol", "")
        desc = (inst.get("description") or "").upper()
        score = -server_rank  # earlier server position -> higher score

        if sym == q_upper:
            score += 200
        elif sym.startswith(q_upper):
            score += 100
        elif q_upper in desc:
            score += 30

        score += cls._SEARCH_TYPE_BIAS.get(inst.get("asset_type", ""), 0)
        score -= len(sym)
        return score

    def _search_tickers_sync(self, query: str, limit: int) -> list[Any]:
        """
        Two-pass synchronous lookup against Polygon's ``/v3/reference/tickers``:

          1. **Exact-ticker pass** (`ticker=<query>` uppercased) — guarantees
             that typing a real symbol like ``QQQ`` or ``SPY`` always
             surfaces the actual ETF as the first result, even when many
             other instruments mention it in their name.
          2. **Fuzzy-search pass** (`search=<query>`) — Polygon's relevance-
             ranked match across both ticker and company name in one call,
             so "apple" -> AAPL and "NVD" -> NVDA both work.

        Results are returned in priority order (exact first), deduped is the
        caller's responsibility. Each pass is capped at ``limit`` items so a
        wildcard search doesn't paginate through the entire US equities
        universe.
        """
        import itertools
        client = self._rest_client()
        results: list[Any] = []

        # Exact-ticker fast path. Skip for free-text queries (lowercase /
        # whitespace) — Polygon's ticker filter is case-sensitive on the
        # server, so passing "apple" here would just waste a round trip.
        q_upper = query.strip().upper()
        if q_upper.isalnum() and len(q_upper) <= 8:
            try:
                it = client.list_tickers(
                    ticker=q_upper,
                    active=True,
                    market="stocks",
                    limit=10,
                )
                results.extend(itertools.islice(it, 5))
            except Exception as e:
                logger.debug(
                    "Polygon list_tickers ticker=%r failed: %s", q_upper, e,
                )

        # Fuzzy relevance pass.
        try:
            it = client.list_tickers(
                search=query,
                active=True,
                market="stocks",
                limit=min(max(limit, 10), 100),
                # `sort` is ignored by Polygon when `search` is set (server
                # orders by relevance) but passing it doesn't hurt.
                sort="ticker",
            )
            results.extend(itertools.islice(it, limit))
        except Exception as e:
            logger.debug("Polygon list_tickers search=%r failed: %s", query, e)

        return results

    async def search_instruments(self, query: str, *, limit: int = 10) -> list[dict]:
        """
        Symbol autocomplete via Polygon's reference API.

        Polygon's ``/v3/reference/tickers?search=...&active=true`` does a
        relevance-ranked match across both ticker symbol and company name in
        a single round trip — so we get "apple" -> AAPL and "NVD" -> NVDA
        with one call. We pull a bit more than ``limit`` (up to 3x), apply a
        small local rerank that prefers EQUITY/ETF + exact/prefix ticker
        hits, then truncate. Returns ``[]`` on any provider error so the UI
        degrades silently — autocomplete failure must never block typing.
        """
        q = (query or "").strip()
        if not q or limit <= 0:
            return []
        q_upper = q.upper()

        # Pull 3x the requested limit (capped at 30) so the rerank has
        # enough headroom to surface EQUITY/ETF matches above warrants /
        # preferreds when Polygon's server ordering returns them first.
        fetch_n = min(max(limit * 3, 10), 30)
        try:
            raw = await asyncio.to_thread(self._search_tickers_sync, q, fetch_n)
        except Exception as e:
            logger.warning("Polygon search_instruments(%r) error: %s", q, e)
            return []

        normalized: list[dict] = []
        for idx, t in enumerate(raw):
            inst = self._normalize_search_ticker(t)
            if inst is None:
                continue
            inst["_server_rank"] = idx
            normalized.append(inst)

        # Dedupe by symbol (Polygon shouldn't return duplicates but be
        # defensive in case different share classes share a base symbol).
        merged: dict[str, dict] = {}
        for inst in normalized:
            merged.setdefault(inst["symbol"], inst)

        ranked = sorted(
            merged.values(),
            key=lambda inst: -self._score_search_result(
                inst, q_upper, inst.pop("_server_rank", 0)
            ),
        )
        return ranked[:limit]

    # ---------- Top movers ----------

    def _fetch_direction_sync(self, direction: str) -> list:
        client = self._rest_client()
        result = client.get_snapshot_direction(
            market_type="stocks", direction=direction
        )
        if result is None:
            return []
        if isinstance(result, list):
            return result
        try:
            return list(result)
        except TypeError:
            return [result]

    @staticmethod
    def _snapshot_to_mover_row(snap: Any) -> Optional[dict]:
        ticker = (getattr(snap, "ticker", None) or "").upper()
        if not ticker:
            return None
        last_trade = getattr(snap, "last_trade", None)
        day = getattr(snap, "day", None)
        prev_day = getattr(snap, "prev_day", None)

        last = getattr(last_trade, "price", None) if last_trade is not None else None
        if last is None and day is not None:
            last = getattr(day, "close", None)

        prev_close = getattr(prev_day, "close", None) if prev_day is not None else None
        volume = getattr(day, "volume", None) if day is not None else None
        trades = getattr(day, "transactions", None) if day is not None else None

        net_change = getattr(snap, "todays_change", None)
        pct = getattr(snap, "todays_change_percent", None)
        if net_change is None and last is not None and prev_close not in (None, 0):
            try:
                net_change = float(last) - float(prev_close)
            except (TypeError, ValueError):
                net_change = None
        if pct is None and net_change is not None and prev_close not in (None, 0):
            try:
                pct = (float(net_change) / float(prev_close)) * 100.0
            except (TypeError, ValueError, ZeroDivisionError):
                pct = None

        direction = None
        if pct is not None:
            direction = "up" if pct >= 0 else "down"

        return {
            "symbol": ticker,
            "description": None,  # Polygon's snapshot omits description
            "lastPrice": float(last) if last is not None else None,
            "netChange": float(net_change) if net_change is not None else None,
            "netPercentChange": float(pct) if pct is not None else None,
            "direction": direction,
            "volume": float(volume) if volume is not None else None,
            "totalVolume": float(volume) if volume is not None else None,
            "trades": int(trades) if trades is not None else None,
            "marketShare": None,
        }

    async def get_movers(
        self,
        symbol_id: str = "",
        *,
        sort: str = "PERCENT_CHANGE_UP",
        frequency: int = 0,
        **_kwargs: Any,
    ) -> dict:
        """
        Top gainers / losers for the entire US stock market.

        Schwab's ``/movers/{index}`` is per-index; Polygon's
        ``/v2/snapshot/locale/us/markets/stocks/{direction}`` is market-wide,
        so ``symbol_id`` and ``frequency`` are accepted for API compatibility
        but intentionally ignored. The returned dict mirrors Schwab's
        ``{"screeners": [...]}`` shape so the existing ``/api/movers`` route
        can normalize Polygon and Schwab responses identically.

        For ``VOLUME`` / ``TRADES`` sorts we fetch *both* gainers and losers
        and let the route's downstream sorter rank by volume/trade count —
        Polygon doesn't have an exchange-wide "top volume" endpoint short of
        snapshot-all (~10k rows), which would be wasteful for a tape widget.
        """
        sort_u = (sort or "").upper().strip()
        directions: list[str]
        if sort_u == "PERCENT_CHANGE_DOWN":
            directions = ["losers"]
        elif sort_u in ("VOLUME", "TRADES"):
            directions = ["gainers", "losers"]
        else:
            directions = ["gainers"]

        screeners: list[dict] = []
        seen: set[str] = set()
        for direction in directions:
            try:
                snaps = await asyncio.to_thread(self._fetch_direction_sync, direction)
            except Exception as e:
                logger.error("Polygon get_movers(%s) failed: %s", direction, e)
                continue
            for snap in snaps:
                row = self._snapshot_to_mover_row(snap)
                if row is None:
                    continue
                sym = row["symbol"]
                if sym in seen:
                    continue
                seen.add(sym)
                screeners.append(row)

        # Polygon's snapshot doesn't include company names, so enrich each
        # screener row with a one-shot lookup against the Reference API. The
        # result is cached, so subsequent refreshes are essentially free for
        # tickers that stick around in gainers/losers across polls.
        if screeners:
            try:
                names = await self._lookup_ticker_names([r["symbol"] for r in screeners])
                for row in screeners:
                    name = names.get(row["symbol"])
                    if name and not row.get("description"):
                        row["description"] = name
            except Exception as e:
                logger.debug("Polygon get_movers: name enrichment failed: %s", e)

        return {"screeners": screeners}

    # ---------- WebSocket streaming ----------

    def _ws_client(self):
        """
        Build (and cache) the massive WebSocketClient on first use. The
        constructor only builds the URL/state; the network connection happens
        inside ``connect()``, which we run on the streamer thread.
        """
        if self._ws is None:
            from massive import WebSocketClient
            self._ws = WebSocketClient(
                api_key=self._api_key,
                feed=self._ws_feed,
                market=self._ws_market,
                secure=self._ws_secure,
                max_reconnects=self._ws_max_reconnects,
            )
        return self._ws

    @staticmethod
    def _agg_to_bar(msg: Any) -> Optional[SimpleNamespace]:
        """
        Convert a Polygon WebSocket EquityAgg / FuturesAgg into the bar
        SimpleNamespace shape that ``WatchlistService._on_bar`` already
        consumes. Returns None for messages without a symbol so the caller
        can drop them silently.
        """
        symbol = (getattr(msg, "symbol", None) or "").strip()
        if not symbol:
            return None

        ts_ms = getattr(msg, "start_timestamp", None)
        if ts_ms is None:
            ts_ms = getattr(msg, "end_timestamp", None)
        if ts_ms is not None:
            ts = datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc)
        else:
            ts = datetime.now(timezone.utc)

        return SimpleNamespace(
            symbol=symbol.upper(),
            ticker=symbol.upper(),
            timestamp=ts,
            ts=ts,
            open=float(getattr(msg, "open", 0) or 0),
            high=float(getattr(msg, "high", 0) or 0),
            low=float(getattr(msg, "low", 0) or 0),
            close=float(getattr(msg, "close", 0) or 0),
            volume=float(getattr(msg, "volume", 0) or 0),
            vwap=float(getattr(msg, "vwap", 0) or 0),
            # Polygon's EquityAgg has no per-minute transaction count;
            # FuturesAgg exposes it as `transactions`. Default to 0 when
            # missing so the batcher writes a clean row.
            trade_count=int(getattr(msg, "transactions", 0) or 0),
        )

    async def _on_messages(self, messages) -> None:
        """
        WebSocket processor — runs on the *streamer thread's* event loop.
        Filters to 1-minute aggregate events (``AM``) and dispatches each bar
        back to the main loop where the OHLCV batcher lives.
        """
        if not self._bar_callback or not self._main_loop:
            return
        if self._main_loop.is_closed():
            return

        for m in messages:
            event_type = getattr(m, "event_type", None)
            # `event_type` may be a string or an Enum (massive uses both).
            if hasattr(event_type, "value"):
                event_type = event_type.value
            if event_type != WS_EVENT_MINUTE_AGG:
                continue
            bar = self._agg_to_bar(m)
            if bar is None:
                continue
            try:
                asyncio.run_coroutine_threadsafe(
                    self._bar_callback(bar),
                    self._main_loop,
                )
            except RuntimeError as e:
                # Main loop probably shutting down; swallow so the streamer
                # thread keeps consuming the socket buffer.
                logger.warning("Polygon: bar dispatch to main loop failed: %s", e)

    def _streamer_thread_target(self) -> None:
        """
        Entry point for the streamer daemon thread. Owns its own event loop
        so the main FastAPI loop is never blocked by the websocket coroutine.
        """
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._streamer_loop = loop
        try:
            client = self._ws_client()
            loop.run_until_complete(client.connect(self._on_messages))
        except Exception as e:
            logger.error("Polygon streamer loop error: %s", e, exc_info=True)
        finally:
            self._streamer_started = False
            self._streamer_loop = None
            try:
                loop.close()
            except Exception:
                pass

    def start_stream(self) -> None:
        """Start the Polygon WebSocket thread. Idempotent."""
        if self._streamer_started:
            logger.debug("Polygon stream already started")
            return
        self._streamer_started = True
        self._streamer_thread = threading.Thread(
            target=self._streamer_thread_target,
            name="polygon-streamer",
            daemon=True,
        )
        self._streamer_thread.start()
        logger.info(
            "Polygon streamer thread started (feed=%s, market=%s)",
            self._ws_feed, self._ws_market,
        )

    def stop_stream(self) -> None:
        """Stop the Polygon WebSocket. Safe to call repeatedly."""
        if not self._streamer_started and self._streamer_thread is None:
            return
        self._streamer_started = False

        loop = self._streamer_loop
        client = self._ws
        if loop is not None and client is not None and not loop.is_closed():
            try:
                asyncio.run_coroutine_threadsafe(client.close(), loop)
            except Exception as e:
                logger.warning("Polygon: close coroutine schedule failed: %s", e)

        thread = self._streamer_thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=5.0)
        self._streamer_thread = None
        logger.info("Polygon stream stopped")

    def subscribe_bars(self, callback: Callable, tickers: list[str]) -> None:
        """
        Subscribe to 1-minute aggregate bars (``AM.<TICKER>``) for the given
        tickers. Must be called from an async context so the main loop can be
        captured for cross-thread dispatch.
        """
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error(
                "Polygon subscribe_bars: no running loop; call from an async context"
            )
            return

        self._bar_callback = callback

        # Compute the *newly* added tickers up-front so we only push diffs to
        # the websocket (re-subscribing the same key is harmless but wasteful,
        # and re-subscribing AM.* from many add_members calls quietly fans out
        # provider RPCs we don't want).
        incoming = [t.strip().upper() for t in tickers if (t or "").strip()]
        already = set(self._subscribed_tickers)
        newly = [t for t in dict.fromkeys(incoming) if t not in already]
        self._subscribed_tickers = list(
            dict.fromkeys(self._subscribed_tickers + newly)
        )

        # Build the websocket client now (cheap, no I/O) so we can register
        # subscriptions synchronously before the thread even starts. The
        # WebSocketClient's connect() reconciles `scheduled_subs` on each tick.
        client = self._ws_client()
        for t in newly:
            client.subscribe(f"{WS_SUB_PREFIX}{t}")

        self.start_stream()
        logger.info("Polygon subscribed to %s; total=%d",
                    newly, len(self._subscribed_tickers))

    def unsubscribe_bars(self, tickers: list[str]) -> None:
        """Unsubscribe from bar updates for given tickers."""
        norm = [t.strip().upper() for t in tickers if (t or "").strip()]
        for t in norm:
            if t in self._subscribed_tickers:
                self._subscribed_tickers.remove(t)
        if self._ws is not None and norm:
            for t in norm:
                self._ws.unsubscribe(f"{WS_SUB_PREFIX}{t}")
        logger.info("Polygon unsubscribed from %s; remaining=%d",
                    norm, len(self._subscribed_tickers))
