"""
Schwab (Think or Swim) data provider.

Implements DataProvider for Charles Schwab Trader API and Streamer API.
OAuth2 and streamer connection details come from Trader API (GET User Preference).
"""
import asyncio
import json
import logging
import threading
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Callable, Optional

import aiohttp
import pandas as pd

from app.providers.base import DataProvider

logger = logging.getLogger(__name__)

# Trader API paths
TOKEN_PATH = "/v1/oauth/token"
USER_PRINCIPALS_PATH = "/v1/userprincipals"
PRICE_HISTORY_PATH = "/v1/marketdata/{symbol}/pricehistory"

# Streamer services
SERVICE_ADMIN = "ADMIN"
SERVICE_CHART_EQUITY = "CHART_EQUITY"
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
    ):
        self._client_id = client_id
        self._client_secret = client_secret
        self._refresh_token = refresh_token
        self._callback_url = callback_url
        self._base_url = base_url.rstrip("/")
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
        payload = {
            "grant_type": "refresh_token",
            "refresh_token": self._refresh_token,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(
                url,
                data=payload,
                headers={"Content-Type": "application/x-www-form-urlencoded"},
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("Schwab token exchange failed: %s %s", resp.status, text)
                    raise RuntimeError(f"Schwab token exchange failed: {resp.status} {text}")
                data = await resp.json()
        with self._token_refresh_lock:
            self._access_token = data.get("access_token")
        if not self._access_token:
            raise RuntimeError("Schwab token response missing access_token")
        logger.info("Schwab access token obtained")
        return self._access_token

    async def _get_user_principals(self) -> dict:
        """
        GET user principals (streamer connection info, subscription keys).
        Required for Streamer API LOGIN and SUBS.
        """
        token = await self._ensure_token()
        url = f"{self._base_url}{USER_PRINCIPALS_PATH}"
        params = {"fields": "streamerSubscriptionKeys,streamerConnectionInfo"}
        headers = {"Authorization": f"Bearer {token}"}
        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 401:
                    self._access_token = None
                    return await self._get_user_principals()
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("Schwab user principals failed: %s %s", resp.status, text)
                    raise RuntimeError(f"Schwab user principals failed: {resp.status} {text}")
                data = await resp.json()
        self._user_prefs = data
        # Resolve streamer WebSocket URL from connection info (structure may vary)
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
        logger.info("Schwab user principals loaded, streamer_url=%s", bool(self._streamer_url))
        return data

    def _streamer_ids(self) -> dict:
        """Return SchwabClientCustomerId and SchwabClientCorrelId for Streamer requests."""
        keys = (self._user_prefs or {}).get("streamerSubscriptionKeys") or {}
        # Handle both direct keys and nested "keys" array (TD/Schwab variants)
        customer_id = keys.get("schwabClientCustomerId")
        correl_id = keys.get("schwabClientCorrelId")
        if not customer_id and isinstance(keys.get("keys"), list) and keys["keys"]:
            first = keys["keys"][0]
            if isinstance(first, dict):
                customer_id = first.get("schwabClientCustomerId") or first.get("key")
                correl_id = first.get("schwabClientCorrelId")
        return {
            "SchwabClientCustomerId": customer_id or "",
            "SchwabClientCorrelId": correl_id or str(uuid.uuid4()),
        }

    def _channel_function_ids(self) -> dict:
        """Return SchwabClientChannel and SchwabClientFunctionId for LOGIN (from prefs or defaults)."""
        prefs = (self._user_prefs or {}).get("preferences") or {}
        return {
            "SchwabClientChannel": prefs.get("streamerChannel") or "N9",
            "SchwabClientFunctionId": prefs.get("streamerFunctionId") or "APIAPP",
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
                    # Subscribe to CHART_EQUITY for current tickers
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
        """Fetch historical bars from Trader API price history endpoint."""
        token = await self._ensure_token()
        path = PRICE_HISTORY_PATH.format(symbol=symbol.upper())
        url = f"{self._base_url}{path}"

        # Schwab uses periodType (day/month/year), period (count), frequencyType (minute/daily), frequency (1,5,10 etc)
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
            "periodType": period_type,
            "period": period,
            "frequencyType": freq_type,
            "frequency": freq,
            "startTime": start_ms,
            "endTime": end_ms,
        }
        headers = {"Authorization": f"Bearer {token}"}

        async with aiohttp.ClientSession() as session:
            async with session.get(url, params=params, headers=headers) as resp:
                if resp.status == 401:
                    self._access_token = None
                    return await self.historical_df(symbol, start, end, timeframe)
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("Schwab price history failed: %s %s", resp.status, text)
                    return pd.DataFrame()
                data = await resp.json()

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
