"""
Schwab (Think or Swim) data provider.

Implements DataProvider for Charles Schwab Trader API and Streamer API.
OAuth2 and streamer connection details come from Trader API (GET User Preference).
"""
import asyncio
import base64
import json
import logging
import os
import threading
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Optional

import aiohttp
import pandas as pd

from app.providers.base import DataProvider

logger = logging.getLogger(__name__)

# Trader API (auth + user preference, accounts, orders, transactions). Base per api_docs/account_access_api.md.
TOKEN_PATH = "/v1/oauth/token"
TRADER_API_BASE = "/trader/v1"
USER_PREFERENCE_PATH = "/trader/v1/userPreference"

# Market Data API - https://api.schwabapi.com/marketdata/v1 (see api_docs/market_data_api.md: quotes, chains, pricehistory, movers, markets, instruments)
MARKET_DATA_BASE = "/marketdata/v1"
PRICE_HISTORY_PATH = "/pricehistory"
QUOTES_PATH = "/quotes"
QUOTE_SINGLE_PATH = "/{symbol_id}/quotes"
CHAINS_PATH = "/chains"
EXPIRATION_CHAIN_PATH = "/expirationchain"
MOVERS_PATH = "/movers/{symbol_id}"
MARKETS_PATH = "/markets"
MARKET_SINGLE_PATH = "/markets/{market_id}"
INSTRUMENTS_PATH = "/instruments"
INSTRUMENT_CUSIP_PATH = "/instruments/{cusip_id}"

# Streamer services (per Schwab Streamer API doc; same request shape: service, command, SchwabClientCustomerId, SchwabClientCorrelId, parameters)
SERVICE_ADMIN = "ADMIN"
SERVICE_CHART_EQUITY = "CHART_EQUITY"
SERVICE_CHART_FUTURES = "CHART_FUTURES"
SERVICE_LEVELONE_EQUITIES = "LEVELONE_EQUITIES"
SERVICE_LEVELONE_OPTIONS = "LEVELONE_OPTIONS"
SERVICE_LEVELONE_FUTURES = "LEVELONE_FUTURES"
SERVICE_LEVELONE_FUTURES_OPTIONS = "LEVELONE_FUTURES_OPTIONS"
SERVICE_LEVELONE_FOREX = "LEVELONE_FOREX"
SERVICE_NYSE_BOOK = "NYSE_BOOK"
SERVICE_NASDAQ_BOOK = "NASDAQ_BOOK"
SERVICE_OPTIONS_BOOK = "OPTIONS_BOOK"
SERVICE_SCREENER_EQUITY = "SCREENER_EQUITY"
SERVICE_SCREENER_OPTION = "SCREENER_OPTION"
SERVICE_ACCT_ACTIVITY = "ACCT_ACTIVITY"
CHART_EQUITY_FIELDS = "0,1,2,3,4,5,7"  # key, open, high, low, close, volume, chartTime


class SchwabProvider(DataProvider):
    """
    Schwab/Think or Swim data provider.

    Uses Trader API for OAuth, user preference (streamer connection info),
    and historical price history. Uses Streamer API (WebSocket) for real-time bars.
    """

    def __init__(
        self,
        client_id: str,
        client_secret: str,
        refresh_token: str = "",
        callback_url: Optional[str] = None,
        base_url: str = "https://api.schwabapi.com",
        refresh_token_file: Optional[str] = None,
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._callback_url = callback_url
        self._base_url = base_url.rstrip("/")
        self._refresh_token_file = refresh_token_file
        self._access_token: Optional[str] = None
        self._streamer_url: Optional[str] = None
        self._user_prefs: Optional[dict] = None
        self._lock = asyncio.Lock()
        self._token_refresh_lock = threading.Lock()
        # Streamer thread and state
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._streamer_thread: Optional[threading.Thread] = None
        self._streamer_started = False
        self._subscribed_tickers: list[str] = []
        self._bar_callback: Optional[Callable] = None

    async def _ensure_token(self) -> str:
        """
        Obtain a valid access token (refresh if needed).
        POST /v1/oauth/token with grant_type=refresh_token.
        """
        with self._token_refresh_lock:
            if self._access_token:
                return self._access_token
            if not self._refresh_token:
                raise ValueError("SCHWAB_REFRESH_TOKEN is required for Schwab provider")
        url = f"{self._base_url}{TOKEN_PATH}"
        # Schwab token endpoint requires client credentials via Basic auth (RFC 6749 2.3.1), not body.
        credentials = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                data=payload,
                headers={
                    "Content-Type": "application/x-www-form-urlencoded",
                    "Authorization": f"Basic {credentials}",
                },
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("Schwab token exchange failed: %s %s", resp.status, text)
                    raise RuntimeError(f"Schwab token exchange failed: {resp.status} {text}")
                data = await resp.json()
        with self._token_refresh_lock:
            self._access_token = data.get("access_token")
            new_refresh = data.get("refresh_token")
            if new_refresh:
                self._refresh_token = new_refresh
                if self._refresh_token_file:
                    try:
                        os.makedirs(os.path.dirname(self._refresh_token_file) or ".", exist_ok=True)
                        with open(self._refresh_token_file, "w") as f:
                            f.write(new_refresh)
                        logger.info("Schwab refresh token persisted to %s", self._refresh_token_file)
                    except OSError as e:
                        logger.warning("Could not persist Schwab refresh token: %s", e)
        if not self._access_token:
            raise RuntimeError("Schwab token response missing access_token")
        logger.info("Schwab access token obtained")
        return self._access_token

    async def _get_user_principals(self) -> dict:
        """
        GET User Preference (streamer connection info, subscription keys).
        Supports both streamerConnectionInfo/streamerSubscriptionKeys and streamerInfo (api_docs/account_access_api.md).
        """
        token = await self._ensure_token()
        url = f"{self._base_url}{USER_PREFERENCE_PATH}"
        params = {"fields": "streamerSubscriptionKeys,streamerConnectionInfo"}
        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 401:
                    self._access_token = None
                    return await self._get_user_principals()
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("Schwab user preference failed: %s %s", resp.status, text)
                    raise RuntimeError(f"Schwab user preference failed: {resp.status} {text}")
                data = await resp.json()
        self._user_prefs = data
        # Resolve streamer WebSocket URL: streamerConnectionInfo (Streamer doc) or streamerInfo (Account Access doc)
        conn_info = data.get("streamerConnectionInfo")
        if isinstance(conn_info, list):
            for node in conn_info:
                self._streamer_url = node.get("uri") or node.get("streamerSocketUrl") or node.get("websocketUrl")
                if self._streamer_url:
                    break
        elif isinstance(conn_info, dict):
            nodes = conn_info.get("streamerConnectionInfo") or conn_info.get("streamerInfo")
            if isinstance(nodes, list):
                for node in nodes:
                    self._streamer_url = node.get("uri") or node.get("streamerSocketUrl") or node.get("websocketUrl")
                    if self._streamer_url:
                        break
            if not self._streamer_url:
                self._streamer_url = conn_info.get("uri") or conn_info.get("streamerSocketUrl")
        if not self._streamer_url:
            streamer_info = data.get("streamerInfo")
            if isinstance(streamer_info, list) and streamer_info:
                node = streamer_info[0]
                self._streamer_url = node.get("streamerSocketUrl") or node.get("uri") or node.get("websocketUrl")
        logger.info("Schwab user preference loaded, streamer_url=%s", bool(self._streamer_url))
        return data

    async def _market_data_get(self, path: str, params: Optional[dict] = None) -> dict:
        """
        Authenticated GET to Market Data API. Path is appended to base + MARKET_DATA_BASE.
        Caller may pass path with placeholders already formatted (e.g. /pricehistory or /AAPL/quotes).
        On 401 clears token and retries once. On non-2xx returns {}. Response shape follows Schwab schemas.
        """
        token = await self._ensure_token()
        url = f"{self._base_url}{MARKET_DATA_BASE}{path}"
        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params or {}, headers=headers) as resp:
                if resp.status == 401:
                    self._access_token = None
                    return await self._market_data_get(path, params)
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("Schwab market data %s failed: %s %s", path, resp.status, text[:200])
                    return {}
                return await resp.json()

    async def _trader_get(self, path: str, params: Optional[dict] = None) -> dict:
        """
        Authenticated GET to Trader API (Account Access). Path is appended to base + TRADER_API_BASE.
        On 401 clears token and retries once. On non-2xx returns {}. Uses Schwab-Client-CorrelID header per doc.
        """
        token = await self._ensure_token()
        url = f"{self._base_url}{TRADER_API_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Schwab-Client-CorrelID": str(uuid.uuid4()),
        }
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params or {}, headers=headers) as resp:
                if resp.status == 401:
                    self._access_token = None
                    return await self._trader_get(path, params)
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("Schwab trader %s failed: %s %s", path, resp.status, text[:200])
                    return {}
                return await resp.json()

    def _streamer_ids(self) -> dict:
        """Return SchwabClientCustomerId and SchwabClientCorrelId for Streamer requests (streamerSubscriptionKeys or streamerInfo)."""
        keys = (self._user_prefs or {}).get("streamerSubscriptionKeys") or {}
        customer_id = keys.get("schwabClientCustomerId")
        correl_id = keys.get("schwabClientCorrelId")
        if not customer_id and isinstance(keys.get("keys"), list) and keys["keys"]:
            first = keys["keys"][0]
            if isinstance(first, dict):
                customer_id = first.get("schwabClientCustomerId") or first.get("key")
                correl_id = first.get("schwabClientCorrelId")
        if not customer_id:
            streamer_info = (self._user_prefs or {}).get("streamerInfo")
            if isinstance(streamer_info, list) and streamer_info:
                node = streamer_info[0]
                customer_id = node.get("schwabClientCustomerId")
                correl_id = node.get("schwabClientCorrelId")
        return {
            "SchwabClientCustomerId": customer_id or "",
            "SchwabClientCorrelId": correl_id or str(uuid.uuid4()),
        }

    def _channel_function_ids(self) -> dict:
        """Return SchwabClientChannel and SchwabClientFunctionId for LOGIN (preferences or streamerInfo)."""
        prefs = (self._user_prefs or {}).get("preferences") or {}
        ch = prefs.get("streamerChannel")
        fn = prefs.get("streamerFunctionId")
        if not ch or not fn:
            streamer_info = (self._user_prefs or {}).get("streamerInfo")
            if isinstance(streamer_info, list) and streamer_info:
                node = streamer_info[0]
                ch = ch or node.get("schwabClientChannel")
                fn = fn or node.get("schwabClientFunctionId")
        return {
            "SchwabClientChannel": ch or "N9",
            "SchwabClientFunctionId": fn or "APIAPP",
        }

    @staticmethod
    def _chart_content_to_bar(content: dict) -> SimpleNamespace:
        """Map CHART_EQUITY content (key, 1–5, 7) to bar object for app contract."""
        ts_ms = content.get(7) or content.get("7")
        if ts_ms is not None:
            ts = datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc)
        else:
            ts = datetime.now(timezone.utc)
        return SimpleNamespace(
            symbol=content.get("key", ""),
            ticker=content.get("key", ""),
            timestamp=ts,
            ts=ts,
            open=float(content.get(1) or content.get("1") or 0),
            high=float(content.get(2) or content.get("2") or 0),
            low=float(content.get(3) or content.get("3") or 0),
            close=float(content.get(4) or content.get("4") or 0),
            volume=int(content.get(5) or content.get("5") or 0),
        )

    async def _run_streamer_loop(self) -> None:
        """Run in streamer thread: connect, LOGIN, SUBS, then receive and dispatch bars."""
        try:
            await self._ensure_token()
            await self._get_user_principals()
        except Exception as e:
            logger.error("Schwab streamer: token/principals failed: %s", e, exc_info=True)
            return
        if not self._streamer_url:
            logger.error("Schwab streamer: no streamer URL in user principals")
            return
        ids = self._streamer_ids()
        ch_fun = self._channel_function_ids()
        token = await self._ensure_token()
        login_req = {
            "requests": [
                {
                    "requestid": "1",
                    "service": SERVICE_ADMIN,
                    "command": "LOGIN",
                    "SchwabClientCustomerId": ids["SchwabClientCustomerId"],
                    "SchwabClientCorrelId": ids["SchwabClientCorrelId"],
                    "parameters": {
                        "Authorization": token,
                        "SchwabClientChannel": ch_fun["SchwabClientChannel"],
                        "SchwabClientFunctionId": ch_fun["SchwabClientFunctionId"],
                    },
                }
            ]
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.ws_connect(self._streamer_url) as ws:
                    await ws.send_str(json.dumps(login_req))
                    login_ok = False
                    while True:
                        msg = await ws.receive()
                        if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                            break
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            data = json.loads(msg.data)
                        except json.JSONDecodeError:
                            continue
                        if "response" in data:
                            for r in data["response"]:
                                content = r.get("content") or {}
                                code = content.get("code")
                                if r.get("service") == SERVICE_ADMIN and r.get("command") == "LOGIN":
                                    if code == 0:
                                        login_ok = True
                                        logger.info("Schwab streamer LOGIN success")
                                    elif code == 3:
                                        logger.error("Schwab streamer LOGIN_DENIED (token invalid/expired): %s", content)
                                    else:
                                        logger.error("Schwab streamer LOGIN failed code=%s: %s", code, content)
                                    break
                                if code == 30:
                                    logger.warning("Schwab streamer STOP_STREAMING (inactivity/admin)")
                                elif code == 19:
                                    logger.warning("Schwab streamer REACHED_SYMBOL_LIMIT")
                                elif code == 12:
                                    logger.warning("Schwab streamer CLOSE_CONNECTION (max connections)")
                            if login_ok:
                                break
                        if "notify" in data:
                            continue
                    if not login_ok:
                        return
                    # Subscribe to CHART_EQUITY for current tickers. Other services (LEVELONE_EQUITIES, etc.)
                    # use the same request format (service, command, SchwabClientCustomerId, SchwabClientCorrelId, parameters)
                    # and can be added by sending additional requests here after LOGIN.
                    tickers = list(self._subscribed_tickers)
                    if tickers and self._bar_callback:
                        subs_req = {
                            "requests": [
                                {
                                    "requestid": "2",
                                    "service": SERVICE_CHART_EQUITY,
                                    "command": "SUBS",
                                    "SchwabClientCustomerId": ids["SchwabClientCustomerId"],
                                    "SchwabClientCorrelId": ids["SchwabClientCorrelId"],
                                    "parameters": {
                                        "keys": ",".join(tickers),
                                        "fields": CHART_EQUITY_FIELDS,
                                    },
                                }
                            ]
                        }
                        await ws.send_str(json.dumps(subs_req))
                        logger.info("Schwab CHART_EQUITY SUBS sent for %s", tickers)
                    # Receive loop
                    while self._streamer_started:
                        msg = await ws.receive()
                        if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                            break
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            data = json.loads(msg.data)
                        except json.JSONDecodeError:
                            continue
                        if "data" not in data or not self._bar_callback or not self._main_loop:
                            continue
                        for block in data["data"]:
                            if block.get("service") != SERVICE_CHART_EQUITY:
                                continue
                            for content in block.get("content") or []:
                                bar = self._chart_content_to_bar(content)
                                if self._main_loop.is_running() and not self._main_loop.is_closed():
                                    asyncio.run_coroutine_threadsafe(
                                        self._bar_callback(bar),
                                        self._main_loop,
                                    )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error("Schwab streamer loop error: %s", e, exc_info=True)
        finally:
            self._streamer_started = False

    def _streamer_thread_target(self) -> None:
        """Entry point for streamer daemon thread: run async loop."""
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._run_streamer_loop())
        finally:
            loop.close()

    def start_stream(self) -> None:
        """Start the Schwab Streamer WebSocket (connect, LOGIN). Called after subscribe_bars or explicitly."""
        if self._streamer_started:
            logger.debug("Schwab stream already started")
            return
        self._streamer_started = True
        self._streamer_thread = threading.Thread(target=self._streamer_thread_target, daemon=True)
        self._streamer_thread.start()
        logger.info("Schwab streamer thread started")

    def stop_stream(self) -> None:
        """Stop the Streamer connection (ADMIN LOGOUT and close WebSocket)."""
        self._streamer_started = False
        self._streamer_thread = None
        logger.info("Schwab stream stopped")

    def subscribe_bars(self, callback: Callable, tickers: list[str]) -> None:
        """Subscribe to CHART_EQUITY for given tickers. Starts stream and sends SUBS after LOGIN."""
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error("Schwab subscribe_bars: no event loop; call from async context")
            return
        self._bar_callback = callback
        self._subscribed_tickers = list(dict.fromkeys(self._subscribed_tickers + tickers))
        logger.info("Schwab subscribing to bars for %s", self._subscribed_tickers)
        self.start_stream()

    def unsubscribe_bars(self, tickers: list[str]) -> None:
        """Unsubscribe from bar updates for given tickers."""
        for t in tickers:
            if t in self._subscribed_tickers:
                self._subscribed_tickers.remove(t)
        logger.info("Schwab unsubscribed from %s; remaining %s", tickers, self._subscribed_tickers)

    async def historical_df(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1Min",
    ) -> pd.DataFrame:
        """Fetch historical bars from Market Data API price history endpoint."""
        if timeframe == "1Min":
            period_type, period, freq_type, freq = "day", 10, "minute", 1
        elif timeframe == "5Min":
            period_type, period, freq_type, freq = "day", 10, "minute", 5
        elif timeframe == "15Min":
            period_type, period, freq_type, freq = "day", 10, "minute", 15
        elif timeframe == "30Min":
            period_type, period, freq_type, freq = "day", 10, "minute", 30
        elif timeframe in ("1D", "1day", "day"):
            period_type, period, freq_type, freq = "month", 1, "daily", 1
        else:
            period_type, period, freq_type, freq = "day", 10, "minute", 1

        start_ms = int(start.timestamp() * 1000) if start.tzinfo else int(start.replace(tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000) if end.tzinfo else int(end.replace(tzinfo=timezone.utc).timestamp() * 1000)

        params = {
            "symbol": symbol.upper(),
            "periodType": period_type,
            "period": period,
            "frequencyType": freq_type,
            "frequency": freq,
            "startTime": start_ms,
            "endTime": end_ms,
        }
        data = await self._market_data_get(PRICE_HISTORY_PATH, params)
        if not data:
            return pd.DataFrame()

        candles = data.get("candles") or []
        if not candles:
            return pd.DataFrame()

        rows = []
        for c in candles:
            ts = c.get("datetime")
            if ts is None:
                ts = c.get("time")
            if ts is not None:
                if isinstance(ts, (int, float)):
                    dt = datetime.fromtimestamp(ts / 1000.0, tz=timezone.utc)
                else:
                    dt = pd.Timestamp(ts).tz_localize("UTC") if getattr(pd.Timestamp(ts), "tz", None) is None else pd.Timestamp(ts)
                rows.append({
                    "timestamp": dt,
                    "open": float(c.get("open", 0)),
                    "high": float(c.get("high", 0)),
                    "low": float(c.get("low", 0)),
                    "close": float(c.get("close", 0)),
                    "volume": int(c.get("volume", 0) or 0),
                })
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame(rows)
        df.set_index("timestamp", inplace=True)
        df.sort_index(inplace=True)
        return df

    # ----- Market Data REST (server: .../marketdata/v1) -----
    # Return shapes follow Schwab API response schemas (QuoteResponse, OptionChain, etc.).

    async def get_quotes(self, symbols: list[str], **kwargs: Any) -> dict:
        """GET /quotes by list of symbols. Optional: fields, indicative. Returns raw QuoteResponse dict."""
        params = {"symbols": ",".join(s.upper() for s in symbols), **kwargs}
        return await self._market_data_get(QUOTES_PATH, params)

    async def get_quote(self, symbol_id: str, **kwargs: Any) -> dict:
        """GET /{symbol_id}/quotes for a single symbol. Optional: fields. Returns raw quote dict."""
        path = QUOTE_SINGLE_PATH.format(symbol_id=symbol_id.upper())
        return await self._market_data_get(path, kwargs if kwargs else None)

    async def get_option_chains(self, symbol: str, **kwargs: Any) -> dict:
        """GET /chains for an optionable symbol. Pass-through: strikeCount, contractType, fromDate, toDate, etc."""
        params = {"symbol": symbol.upper(), **kwargs}
        return await self._market_data_get(CHAINS_PATH, params)

    async def get_expiration_chain(self, symbol: str, **kwargs: Any) -> dict:
        """GET /expirationchain for an optionable symbol. Returns expiration chain dict."""
        params = {"symbol": symbol.upper(), **kwargs}
        return await self._market_data_get(EXPIRATION_CHAIN_PATH, params)

    async def get_movers(self, symbol_id: str, **kwargs: Any) -> dict:
        """GET /movers/{symbol_id} for a specific index. Optional query params per doc."""
        path = MOVERS_PATH.format(symbol_id=symbol_id.upper())
        return await self._market_data_get(path, kwargs if kwargs else None)

    async def get_market_hours(self, market_id: Optional[str] = None) -> dict:
        """GET /markets or GET /markets/{market_id}. Returns hours dict per Schwab schema."""
        if market_id:
            path = MARKET_SINGLE_PATH.format(market_id=market_id)
            return await self._market_data_get(path, None)
        return await self._market_data_get(MARKETS_PATH, None)

    async def get_instruments(self, symbols: list[str], projection: str, **kwargs: Any) -> dict:
        """GET /instruments by symbols and projection. Pass-through kwargs. Returns InstrumentResponse dict."""
        params = {"symbol": ",".join(s.upper() for s in symbols), "projection": projection, **kwargs}
        return await self._market_data_get(INSTRUMENTS_PATH, params)

    async def get_instrument(self, cusip_id: str) -> dict:
        """GET /instruments/{cusip_id} by CUSIP. Returns instrument dict."""
        path = INSTRUMENT_CUSIP_PATH.format(cusip_id=cusip_id)
        return await self._market_data_get(path, None)

    # ----- Trader API (Account Access) – data only, no order entry -----
    # See api_docs/account_access_api.md. Use hashValue from get_account_numbers() for accountNumber if API requires.

    async def get_account_numbers(self) -> dict:
        """GET /accounts/accountNumbers. Returns list of {accountNumber, hashValue} for use in subsequent account calls."""
        return await self._trader_get("/accounts/accountNumbers")

    async def get_accounts(self) -> dict:
        """GET /accounts. Returns linked account(s) with balances and positions for the logged-in user."""
        return await self._trader_get("/accounts")

    async def get_account(self, account_number: str) -> dict:
        """GET /accounts/{accountNumber}. Returns balance and positions for one account. Use hashValue if API requires."""
        path = f"/accounts/{account_number}"
        return await self._trader_get(path)

    async def get_orders(self, account_number: Optional[str] = None) -> dict:
        """GET /accounts/{accountNumber}/orders or GET /orders. Returns orders for one account or all accounts."""
        if account_number:
            path = f"/accounts/{account_number}/orders"
        else:
            path = "/orders"
        return await self._trader_get(path)

    async def get_transactions(self, account_number: str) -> dict:
        """GET /accounts/{accountNumber}/transactions. Returns transaction history for the account."""
        path = f"/accounts/{account_number}/transactions"
        return await self._trader_get(path)
