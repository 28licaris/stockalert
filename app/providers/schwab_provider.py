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
import random
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Callable, Iterable, Optional
from urllib.parse import urlparse

import aiohttp
from aiohttp.client_exceptions import ClientConnectorError
import pandas as pd

from app.providers.base import DataProvider

logger = logging.getLogger(__name__)

# #region agent log
def _debug_ndjson(message: str, data: dict, hypothesis_id: str = "H1") -> None:
    import json
    import time

    try:
        payload = {
            "sessionId": "98e2bc",
            "timestamp": int(time.time() * 1000),
            "message": message,
            "data": data,
            "hypothesisId": hypothesis_id,
        }
        with open(
            "/Users/licaris/dev/stockalert/stockalert/.cursor/debug-98e2bc.log",
            "a",
            encoding="utf-8",
        ) as f:
            f.write(json.dumps(payload) + "\n")
    except Exception:
        pass


# #endregion

# REST calls must not hang forever on a stuck TLS/socket (common without an explicit timeout).
_HTTP_TIMEOUT = aiohttp.ClientTimeout(total=90, connect=20, sock_connect=20, sock_read=60)
# WebSocket: handshake + long gaps between frames during quiet markets.
_WS_HANDSHAKE_TIMEOUT = aiohttp.ClientWSTimeout(ws_close=60, ws_receive=600)


# Frame types that mean the websocket is over (CLOSING/CLOSED follow a CLOSE).
_WS_END_TYPES = (
    aiohttp.WSMsgType.CLOSE,
    aiohttp.WSMsgType.CLOSING,
    aiohttp.WSMsgType.CLOSED,
    aiohttp.WSMsgType.ERROR,
)


def reconnect_delay(
    attempt: int,
    *,
    base: float,
    cap: float,
    jitter: float = 0.2,
    rand: Callable[[], float] = random.random,
) -> float:
    """Seconds to wait before streamer reconnect number ``attempt`` (0-based).

    Exponential from ``base`` and capped at ``cap``, with +/-``jitter``
    (fraction) so a fleet of reconnects does not synchronise. Never exceeds
    ``cap`` and never negative. ``rand`` is injectable for tests.
    """
    raw = min(cap, base * (2 ** max(0, attempt)))
    delay = raw * (1.0 + jitter * (2.0 * rand() - 1.0))
    return max(0.0, min(cap, delay))

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


def _query_params(params: Optional[dict]) -> dict:
    if not params:
        return {}
    normalized: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, bool):
            normalized[key] = "true" if value else "false"
        else:
            normalized[key] = value
    return normalized


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
# Schwab CHART_EQUITY fields (empirically validated against live data; the local docs copy is misleading):
#   0=key, 1=Sequence, 2=Open, 3=High, 4=Low, 5=Close, 6=Volume, 7=ChartTime(ms), 8=ChartDay
CHART_EQUITY_FIELDS = "0,2,3,4,5,6,7"
# CHART_FUTURES uses the same field IDs as CHART_EQUITY in the Schwab streamer
# protocol (0=key, 2-7 OHLCV+time). Verified against live /MNQ… contracts.
CHART_FUTURES_FIELDS = CHART_EQUITY_FIELDS

# Schwab delivers ticks for only the FIRST 300 keys of a chart subscription and
# silently drops the rest — no rejection, no REACHED_SYMBOL_LIMIT, and
# /stream/status still reports every key as subscribed.
#
# Measured 2026-11-03 on a 424-symbol universe: exactly 293 symbols received
# bars, and the 131 that did not were precisely the 9 symbols Schwab has no
# data for at all plus *every* symbol sorted at or after "S" — i.e. the tail
# past index 300 of the comma-joined key list. A clean truncation, not an
# entitlement cap.
#
# Subscriptions are therefore chunked. The first chunk uses SUBS (which
# REPLACES the service's subscription) and every chunk after it uses ADD
# (which appends) — sending SUBS more than once would wipe the earlier chunks.
MAX_STREAM_KEYS_PER_REQUEST = 300

_DEFAULT_SCHWAB_API_BASE = "https://api.schwabapi.com"


def _normalize_schwab_base_url(base_url: str) -> str:
    """Schwab API origin; empty/whitespace falls back to production (avoids relative URLs and DNS errors)."""
    bu = (base_url or "").strip().rstrip("/")
    return bu if bu else _DEFAULT_SCHWAB_API_BASE.rstrip("/")


def _schwab_refresh_token_user_hint(status: int, body: str) -> str:
    """If Schwab rejects refresh, return a short user-facing hint (no secrets)."""
    if status != 400 or not (body or "").strip():
        return ""
    low = body.lower()
    if "refresh_token_authentication" in low or "unsupported_token_type" in low:
        return (
            "Schwab rejected this refresh token (often expired after ~7 days, revoked, or issued for a "
            "different SCHWAB_CLIENT_ID). Re-authorize and refresh the token:\n"
            "  poetry run python scripts/schwab_get_refresh_token.py\n"
            "If SCHWAB_REFRESH_TOKEN is set in .env, it overrides SCHWAB_REFRESH_TOKEN_FILE — update or "
            "unset it so the new file token is used."
        )
    return ""


def _schwab_user_preference_401_hint() -> str:
    """After token refresh, 401 on GET userPreference usually means app or account linkage (Schwab Trader API doc)."""
    return (
        "GET /trader/v1/userPreference returned 401 after refreshing the access token. Typical causes:\n"
        "  • Developer Portal: enable the Trader API (Individual) product for this app, not Market Data alone.\n"
        "  • OAuth consent: link at least one brokerage account to the app during sign-in.\n"
        "  • Re-authorize: poetry run python scripts/schwab_get_refresh_token.py\n"
    )


class SchwabProvider(DataProvider):
    """
    Schwab/Think or Swim data provider.

    Uses Trader API for OAuth. Market Data REST (e.g. price history, quotes) needs only the
    access token. User Preference is fetched only for the Streamer WebSocket (live bars).
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
        self._base_url = _normalize_schwab_base_url(base_url)
        self._refresh_token_file = refresh_token_file
        self._access_token: Optional[str] = None
        self._streamer_url: Optional[str] = None
        self._user_prefs: Optional[dict] = None
        # One asyncio.Lock per running event loop so concurrent API calls cannot
        # each POST /oauth/token (Schwab may rotate refresh tokens on every exchange).
        self._token_async_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}
        # Streamer thread and state
        self._main_loop: Optional[asyncio.AbstractEventLoop] = None
        self._streamer_thread: Optional[threading.Thread] = None
        self._streamer_started = False
        self._subscribed_tickers: list[str] = []
        self._bar_callback: Optional[Callable] = None
        self._streamer_cmd_q: Optional[asyncio.Queue] = None
        self._streamer_ws: Optional[aiohttp.ClientWebSocketResponse] = None
        self._streamer_loop: Optional[asyncio.AbstractEventLoop] = None
        self._streamer_ready = threading.Event()
        # Supervision state. `_streamer_started` is the DESIRED state (set by
        # start_stream, cleared only by stop_stream); `_stop_evt` interrupts a
        # reconnect backoff sleep. Everything below is observability.
        self._stop_evt = threading.Event()
        self._started_mono: Optional[float] = None       # supervisor start
        self._login_mono: Optional[float] = None         # latest successful LOGIN
        self._last_frame_mono: Optional[float] = None    # any frame, incl. heartbeat
        self._last_bar_mono: Optional[float] = None      # latest bar dispatched
        self._reconnect_count = 0
        self._last_exit_reason: Optional[str] = None
        self._last_exit_at: Optional[datetime] = None
        self._forced_reason: Optional[str] = None        # set by request_reconnect

    def _token_lock_for_running_loop(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        lock = self._token_async_locks.get(loop)
        if lock is None:
            lock = asyncio.Lock()
            self._token_async_locks[loop] = lock
        return lock

    async def _ensure_token(self) -> str:
        """
        Obtain a valid access token (refresh if needed).
        POST /v1/oauth/token with grant_type=refresh_token.
        """
        async with self._token_lock_for_running_loop():
            if self._access_token:
                return self._access_token
            if not self._refresh_token:
                raise ValueError("SCHWAB_REFRESH_TOKEN is required for Schwab provider")
            url = f"{self._base_url}{TOKEN_PATH}"
            parsed = urlparse(url)
            if not parsed.scheme or not parsed.netloc:
                raise ValueError(
                    f"Invalid SCHWAB_BASE_URL / base_url (got host={parsed.netloc!r}). "
                    f"Use a full URL such as {_DEFAULT_SCHWAB_API_BASE}."
                )
            # Schwab token endpoint requires client credentials via Basic auth (RFC 6749 2.3.1), not body.
            credentials = base64.b64encode(f"{self._client_id}:{self._client_secret}".encode()).decode()
            payload = {
                "grant_type": "refresh_token",
                "refresh_token": self._refresh_token,
            }
            try:
                async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
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
                            hint = _schwab_refresh_token_user_hint(resp.status, text)
                            logger.error("Schwab token exchange failed: %s %s", resp.status, text)
                            base_msg = f"Schwab token exchange failed: {resp.status} {text}"
                            raise RuntimeError(f"{hint}\n\n{base_msg}" if hint else base_msg)
                        data = await resp.json()
            except ClientConnectorError as e:
                raise RuntimeError(
                    f"Cannot connect to Schwab at {url} ({e}). "
                    "Check network, DNS, and VPN. If SCHWAB_BASE_URL is in .env, it must be a full URL "
                    f"(e.g. {_DEFAULT_SCHWAB_API_BASE}), not empty."
                ) from None
            self._access_token = data.get("access_token")
            new_refresh = data.get("refresh_token")
            if new_refresh:
                self._refresh_token = new_refresh
                if self._refresh_token_file:
                    try:
                        os.makedirs(os.path.dirname(self._refresh_token_file) or ".", exist_ok=True)
                        with open(self._refresh_token_file, "w") as f:
                            f.write(new_refresh)
                        logger.debug("Schwab refresh token persisted to %s", self._refresh_token_file)
                    except OSError as e:
                        logger.warning("Could not persist Schwab refresh token: %s", e)
            if not self._access_token:
                raise RuntimeError("Schwab token response missing access_token")
            logger.debug("Schwab access token obtained")
            return self._access_token

    async def _get_user_principals(self, _retry_on_401: bool = True) -> dict:
        """
        GET /trader/v1/userPreference. Per Schwab docs the endpoint takes NO query parameters
        and returns a list wrapping {accounts, streamerInfo, offers}. We previously sent a
        TD-Ameritrade-style ?fields=... query which Schwab rejects with 401 Client not authorized.
        """
        token = await self._ensure_token()
        url = f"{self._base_url}{USER_PREFERENCE_PATH}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Schwab-Client-CorrelID": str(uuid.uuid4()),
            "Accept": "application/json",
        }
        try:
            async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 401:
                        if not _retry_on_401:
                            text = await resp.text()
                            hint = _schwab_user_preference_401_hint()
                            msg = f"Schwab user preference unauthorized after token refresh: {text[:500]}"
                            raise RuntimeError(f"{hint}\n{msg}")
                        self._access_token = None
                        return await self._get_user_principals(_retry_on_401=False)
                    if resp.status != 200:
                        text = await resp.text()
                        logger.error("Schwab user preference failed: %s %s", resp.status, text)
                        raise RuntimeError(f"Schwab user preference failed: {resp.status} {text}")
                    data = await resp.json()
        except ClientConnectorError as e:
            raise RuntimeError(
                f"Cannot reach Schwab Trader API at {url} ({e}). "
                "Check DNS/network/VPN; try: ping api.schwabapi.com"
            ) from None
        # Schwab returns either a list [{...}] or a dict {...}; normalize to a dict.
        prefs = data[0] if isinstance(data, list) and data else data
        if not isinstance(prefs, dict):
            raise RuntimeError(f"Schwab userPreference returned unexpected shape: {type(data).__name__}")
        self._user_prefs = prefs
        streamer_info = prefs.get("streamerInfo")
        if isinstance(streamer_info, list) and streamer_info:
            node = streamer_info[0]
            self._streamer_url = (
                node.get("streamerSocketUrl") or node.get("uri") or node.get("websocketUrl")
            )
        logger.info("Schwab user preference loaded, streamer_url=%s", bool(self._streamer_url))
        return prefs

    async def _market_data_get(
        self, path: str, params: Optional[dict] = None, _retry_on_401: bool = True
    ) -> dict:
        """
        Authenticated GET to Market Data API. Path is appended to base + MARKET_DATA_BASE.
        Caller may pass path with placeholders already formatted (e.g. /pricehistory or /AAPL/quotes).
        On 401 clears token and retries at most once. On non-2xx returns {}.
        """
        token = await self._ensure_token()
        url = f"{self._base_url}{MARKET_DATA_BASE}{path}"
        headers = {"Authorization": f"Bearer {token}"}
        query_params = _query_params(params)
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            async with session.get(url, params=query_params, headers=headers) as resp:
                # #region agent log
                _debug_ndjson(
                    "market_data_get",
                    {"path": path, "status": resp.status, "retryOn401": _retry_on_401},
                    "H1",
                )
                # #endregion
                if resp.status == 401:
                    if not _retry_on_401:
                        await resp.text()
                        logger.error("Schwab market data %s unauthorized after token refresh", path)
                        return {}
                    self._access_token = None
                    return await self._market_data_get(path, params, _retry_on_401=False)
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("Schwab market data %s failed: %s %s", path, resp.status, text[:200])
                    return {}
                return await resp.json()

    async def _trader_get(
        self, path: str, params: Optional[dict] = None, _retry_on_401: bool = True
    ) -> dict:
        """
        Authenticated GET to Trader API (Account Access). Path is appended to base + TRADER_API_BASE.
        On 401 clears token and retries at most once. On non-2xx returns {}.
        """
        token = await self._ensure_token()
        url = f"{self._base_url}{TRADER_API_BASE}{path}"
        headers = {
            "Authorization": f"Bearer {token}",
            "Schwab-Client-CorrelID": str(uuid.uuid4()),
        }
        async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
            async with session.get(url, params=params or {}, headers=headers) as resp:
                # #region agent log
                _debug_ndjson(
                    "trader_get",
                    {"path": path, "status": resp.status, "retryOn401": _retry_on_401},
                    "H1",
                )
                # #endregion
                if resp.status == 401:
                    if not _retry_on_401:
                        await resp.text()
                        logger.error("Schwab trader %s unauthorized after token refresh", path)
                        return {}
                    self._access_token = None
                    return await self._trader_get(path, params, _retry_on_401=False)
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
        """
        Map CHART_EQUITY / CHART_FUTURES content to a bar object.
        Schwab field IDs: 0=key, 2=Open, 3=High, 4=Low, 5=Close, 6=Volume, 7=ChartTime(ms).
        Field 1 is Sequence, not Open — Schwab's public field tables list these incorrectly.
        """
        def _f(key: int) -> Any:
            return content.get(key) if content.get(key) is not None else content.get(str(key))

        ts_ms = _f(7)
        if ts_ms is not None:
            ts = datetime.fromtimestamp(int(ts_ms) / 1000.0, tz=timezone.utc)
        else:
            ts = datetime.now(timezone.utc)
        return SimpleNamespace(
            symbol=content.get("key", ""),
            ticker=content.get("key", ""),
            timestamp=ts,
            ts=ts,
            open=float(_f(2) or 0),
            high=float(_f(3) or 0),
            low=float(_f(4) or 0),
            close=float(_f(5) or 0),
            volume=int(_f(6) or 0),
        )

    @staticmethod
    def partition_chart_stream_keys(tickers: Iterable[str]) -> tuple[list[str], list[str]]:
        """
        Schwab streamer uses CHART_EQUITY for stocks/ETFs/indexes and CHART_FUTURES
        for roots that start with ``/`` (e.g. ``/MNQM26``). Keys must not be mixed
        across services in a single SUBS request.
        """
        equity: list[str] = []
        futures: list[str] = []
        for raw in tickers or []:
            s = (raw or "").strip().upper()
            if not s:
                continue
            if s.startswith("/"):
                futures.append(s)
            else:
                equity.append(s)
        return equity, futures

    @staticmethod
    def build_chart_subscribe_requests(
        service: str,
        keys: list[str],
        fields: str,
        *,
        ids: dict | None = None,
        first_request_id: int = 2,
    ) -> list[dict]:
        """Chunk `keys` into Schwab chart subscribe requests.

        First chunk SUBS, the rest ADD. SUBS replaces a service's whole
        subscription, so issuing it per chunk would leave only the last one
        active — the failure this function exists to prevent, alongside the
        300-key truncation itself (see MAX_STREAM_KEYS_PER_REQUEST).
        """
        out: list[dict] = []
        for i in range(0, len(keys), MAX_STREAM_KEYS_PER_REQUEST):
            chunk = keys[i : i + MAX_STREAM_KEYS_PER_REQUEST]
            req = {
                "requestid": str(first_request_id + len(out)),
                "service": service,
                "command": "SUBS" if i == 0 else "ADD",
                "parameters": {"keys": ",".join(chunk), "fields": fields},
            }
            if ids:
                req["SchwabClientCustomerId"] = ids["SchwabClientCustomerId"]
                req["SchwabClientCorrelId"] = ids["SchwabClientCorrelId"]
            out.append(req)
        return out

    def _enqueue_streamer_cmd(self, cmd: dict) -> None:
        """
        Enqueue a streamer command (e.g., SUBS/UNSUBS) for the streamer thread.
        Safe to call from any thread.
        """
        if not self._streamer_loop or not self._streamer_cmd_q:
            return

        def _put_nowait() -> None:
            try:
                self._streamer_cmd_q.put_nowait(cmd)
            except Exception:
                logger.debug("Schwab streamer: failed to enqueue command", exc_info=True)

        try:
            self._streamer_loop.call_soon_threadsafe(_put_nowait)
        except Exception:
            logger.debug("Schwab streamer: call_soon_threadsafe failed", exc_info=True)

    async def _streamer_send(self, payload: dict) -> None:
        ws = self._streamer_ws
        if not ws or ws.closed:
            return
        await ws.send_str(json.dumps(payload))

    async def _streamer_cmd_loop(self, ids: dict) -> None:
        """
        Process queued streamer commands and send them over the active WebSocket.
        Commands are dicts like:
          {"service": "...", "command": "SUBS"/"UNSUBS", "parameters": {...}}
        """
        assert self._streamer_cmd_q is not None
        while self._streamer_started:
            cmd = await self._streamer_cmd_q.get()
            if not isinstance(cmd, dict):
                continue
            if cmd.get("_type") == "STOP":
                break

            payload = {
                "requests": [
                    {
                        "requestid": cmd.get("requestid") or str(uuid.uuid4()),
                        "service": cmd.get("service"),
                        "command": cmd.get("command"),
                        "SchwabClientCustomerId": ids["SchwabClientCustomerId"],
                        "SchwabClientCorrelId": ids["SchwabClientCorrelId"],
                        "parameters": cmd.get("parameters") or {},
                    }
                ]
            }
            try:
                await self._streamer_send(payload)
            except Exception:
                logger.warning("Schwab streamer: send command failed", exc_info=True)

    # ------------------------------------------------------------------
    # Streamer supervision
    #
    # `_streamer_started` is the DESIRED state (start_stream sets it, only
    # stop_stream clears it). One *session* = connect, LOGIN, SUBS, receive;
    # when a session ends for any reason other than a requested stop, the
    # supervisor (`_streamer_thread_target`) logs why and reconnects with
    # backoff. Before this existed a websocket close was a silent, permanent
    # stop (2026-11-09: bars froze at Friday's close until a manual restart).
    # ------------------------------------------------------------------

    @staticmethod
    def _stream_tunables() -> dict:
        from app.config import settings

        return {
            "base": float(getattr(settings, "schwab_streamer_reconnect_base_s", 2.0)),
            "cap": float(getattr(settings, "schwab_streamer_reconnect_cap_s", 300.0)),
            "healthy_after": float(getattr(settings, "schwab_streamer_healthy_after_s", 60.0)),
            "idle": float(getattr(settings, "schwab_streamer_idle_timeout_s", 120.0)),
        }

    @staticmethod
    def _ws_close_detail(ws, msg) -> str:
        """One line describing how/why a websocket ended (for the close log)."""
        try:
            exc = ws.exception() if msg.type == aiohttp.WSMsgType.ERROR else None
        except Exception:  # noqa: BLE001 — diagnostics must not raise
            exc = None
        data = str(getattr(msg, "data", "") or "")[:160]
        return (
            f"frame={msg.type.name} close_code={getattr(ws, 'close_code', None)} "
            f"data={data!r} exc={exc!r}"
        )

    @staticmethod
    def _log_response_frames(responses: list) -> None:
        """Log every post-login response frame (SUBS/ADD/... acks). A non-zero
        code is a WARNING with its meaning; code 19 (symbol limit) and 12/30
        (connection closed / streaming stopped) were previously invisible."""
        names = {12: "CLOSE_CONNECTION", 19: "REACHED_SYMBOL_LIMIT", 30: "STOP_STREAMING"}
        for r in responses or []:
            content = r.get("content") or {}
            code = content.get("code")
            svc, cmd = r.get("service"), r.get("command")
            if code in (None, 0):
                logger.info("Schwab streamer ack: %s %s ok", svc, cmd)
            else:
                logger.warning(
                    "Schwab streamer ack: %s %s code=%s (%s) msg=%s",
                    svc, cmd, code, names.get(code, "unknown"),
                    str(content.get("msg"))[:200],
                )

    @staticmethod
    def _log_notify_frames(notifies: list) -> None:
        """Heartbeats are expected and silent; any other notify is logged."""
        for n in notifies or []:
            if isinstance(n, dict) and "heartbeat" in n:
                continue
            logger.warning("Schwab streamer notify: %s", str(n)[:300])

    async def _run_streamer_loop(self) -> str:
        """Run ONE streamer session in the streamer thread: connect, LOGIN,
        SUBS, then receive and dispatch bars until it ends.

        Returns a short reason string for why the session ended; the
        supervisor logs it and decides whether to reconnect. This never
        changes `_streamer_started` (the desired state).
        """
        try:
            await self._ensure_token()
            await self._get_user_principals()
        except Exception as e:
            logger.error("Schwab streamer: token/principals failed: %s", e, exc_info=True)
            return "token_failed"
        if not self._streamer_url:
            logger.error("Schwab streamer: no streamer URL in user principals")
            return "no_streamer_url"
        tun = self._stream_tunables()
        ids = self._streamer_ids()
        ch_fun = self._channel_function_ids()
        token = await self._ensure_token()
        self._streamer_loop = asyncio.get_running_loop()
        self._streamer_cmd_q = asyncio.Queue()
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
        reason = "ended"
        cmd_task: Optional[asyncio.Task] = None
        try:
            async with aiohttp.ClientSession(timeout=_HTTP_TIMEOUT) as session:
                async with session.ws_connect(
                    self._streamer_url,
                    timeout=_WS_HANDSHAKE_TIMEOUT,
                ) as ws:
                    self._streamer_ws = ws
                    await ws.send_str(json.dumps(login_req))
                    login_ok = False
                    login_answered = False
                    while not login_answered:
                        try:
                            msg = await asyncio.wait_for(ws.receive(), timeout=tun["idle"])
                        except asyncio.TimeoutError:
                            logger.warning(
                                "Schwab streamer: no LOGIN reply in %.0fs", tun["idle"],
                            )
                            return "login_timeout"
                        if msg.type in _WS_END_TYPES:
                            logger.warning(
                                "Schwab streamer: closed before LOGIN completed (%s)",
                                self._ws_close_detail(ws, msg),
                            )
                            return "closed_before_login"
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        try:
                            data = json.loads(msg.data)
                        except json.JSONDecodeError:
                            continue
                        for r in data.get("response") or []:
                            content = r.get("content") or {}
                            code = content.get("code")
                            if r.get("service") == SERVICE_ADMIN and r.get("command") == "LOGIN":
                                login_answered = True
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
                    if not login_ok:
                        return "login_failed"
                    now_m = time.monotonic()
                    self._login_mono = now_m
                    self._last_frame_mono = now_m
                    self._streamer_ready.set()
                    cmd_task = asyncio.create_task(self._streamer_cmd_loop(ids))

                    # Initial subscription snapshot (replayed in full on every reconnect)
                    tickers = list(self._subscribed_tickers)
                    if tickers and self._bar_callback:
                        eq_keys, fut_keys = self.partition_chart_stream_keys(tickers)
                        requests: list[dict] = []
                        if eq_keys:
                            requests.extend(self.build_chart_subscribe_requests(
                                SERVICE_CHART_EQUITY, eq_keys, CHART_EQUITY_FIELDS,
                                ids=ids, first_request_id=2,
                            ))
                        if fut_keys:
                            requests.extend(self.build_chart_subscribe_requests(
                                SERVICE_CHART_FUTURES, fut_keys, CHART_FUTURES_FIELDS,
                                ids=ids, first_request_id=100,
                            ))
                        if len(requests) > 2:
                            logger.info(
                                "Schwab streamer: %d symbols chunked into %d requests "
                                "(cap %d keys/request)",
                                len(eq_keys) + len(fut_keys), len(requests),
                                MAX_STREAM_KEYS_PER_REQUEST,
                            )
                        if requests:
                            await self._streamer_send({"requests": requests})
                        if eq_keys:
                            logger.info("Schwab CHART_EQUITY SUBS sent for %s", eq_keys)
                        if fut_keys:
                            logger.info("Schwab CHART_FUTURES SUBS sent for %s", fut_keys)

                    # Receive loop. Every exit path sets a reason that the
                    # supervisor logs; a dead-silent socket is caught by the
                    # idle timeout (Schwab heartbeats ~every 10 s).
                    reason = "stopped"
                    while self._streamer_started and not self._stop_evt.is_set():
                        try:
                            msg = await asyncio.wait_for(ws.receive(), timeout=tun["idle"])
                        except asyncio.TimeoutError:
                            reason = "idle_timeout"
                            logger.warning(
                                "Schwab streamer: no frame (not even a heartbeat) in %.0fs; "
                                "treating the socket as dead",
                                tun["idle"],
                            )
                            break
                        if msg.type in _WS_END_TYPES:
                            forced, self._forced_reason = self._forced_reason, None
                            reason = forced or f"ws_{msg.type.name.lower()}"
                            logger.warning(
                                "Schwab streamer: websocket ended reason=%s (%s)",
                                reason, self._ws_close_detail(ws, msg),
                            )
                            break
                        if msg.type != aiohttp.WSMsgType.TEXT:
                            continue
                        self._last_frame_mono = time.monotonic()
                        try:
                            data = json.loads(msg.data)
                        except json.JSONDecodeError:
                            logger.warning("Schwab streamer: undecodable frame %r", str(msg.data)[:120])
                            continue
                        if "response" in data:
                            self._log_response_frames(data["response"])
                        if "notify" in data:
                            self._log_notify_frames(data["notify"])
                        if "data" not in data or not self._bar_callback or not self._main_loop:
                            continue
                        dispatched = 0
                        for block in data["data"]:
                            svc = block.get("service")
                            if svc not in (SERVICE_CHART_EQUITY, SERVICE_CHART_FUTURES):
                                continue
                            for content in block.get("content") or []:
                                bar = self._chart_content_to_bar(content)
                                if self._main_loop.is_running() and not self._main_loop.is_closed():
                                    asyncio.run_coroutine_threadsafe(
                                        self._bar_callback(bar),
                                        self._main_loop,
                                    )
                                    dispatched += 1
                        if dispatched:
                            self._last_bar_mono = time.monotonic()
        except asyncio.CancelledError:
            reason = "cancelled"
        except Exception as e:  # noqa: BLE001 — boundary; supervisor reconnects
            logger.error("Schwab streamer loop error: %s", e, exc_info=True)
            reason = f"error:{type(e).__name__}"
        finally:
            if cmd_task is not None and not cmd_task.done():
                cmd_task.cancel()
                await asyncio.gather(cmd_task, return_exceptions=True)
            self._streamer_ready.clear()
            self._streamer_ws = None
            self._streamer_cmd_q = None
            self._streamer_loop = None
        return reason

    def _streamer_thread_target(self) -> None:
        """Supervisor (streamer daemon thread): run a session, and when it
        ends for any reason but a requested stop, log why and reconnect with
        exponential backoff. The attempt counter resets once a session stayed
        up `healthy_after` seconds, so a flapping link backs off while a
        nightly Schwab-side close reconnects in ~2 s."""
        tun = self._stream_tunables()
        attempt = 0
        while self._streamer_started and not self._stop_evt.is_set():
            self._login_mono = None
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                reason = loop.run_until_complete(self._run_streamer_loop()) or "ended"
            except Exception:  # noqa: BLE001 — the supervisor must outlive a bad session
                logger.exception("Schwab streamer: session crashed")
                reason = "crash"
            finally:
                loop.close()
            if not self._streamer_started or self._stop_evt.is_set():
                break
            login = self._login_mono
            healthy = login is not None and (time.monotonic() - login) >= tun["healthy_after"]
            attempt = 0 if healthy else attempt + 1
            delay = reconnect_delay(attempt, base=tun["base"], cap=tun["cap"])
            self._reconnect_count += 1
            self._last_exit_reason = reason
            self._last_exit_at = datetime.now(timezone.utc)
            logger.warning(
                "Schwab streamer: session ended reason=%s up=%s; reconnect #%d in %.1fs (attempt %d)",
                reason,
                "never" if login is None else f"{time.monotonic() - login:.0f}s",
                self._reconnect_count, delay, attempt,
            )
            if self._stop_evt.wait(delay):
                break
        logger.info("Schwab streamer: supervisor exiting")

    def _close_streamer_ws(self) -> bool:
        """Close the live websocket from any thread (wakes a pending receive)."""
        loop, ws = self._streamer_loop, self._streamer_ws
        if loop is None or ws is None or ws.closed:
            return False
        try:
            asyncio.run_coroutine_threadsafe(ws.close(), loop)
            return True
        except Exception:  # noqa: BLE001 — best effort; the idle timeout is the backstop
            logger.warning("Schwab streamer: could not close websocket", exc_info=True)
            return False

    def request_reconnect(self, reason: str) -> bool:
        """Ask the supervisor to drop and re-establish the connection (used by
        the stale-stream watchdog). False when there is no live socket to
        close (already reconnecting)."""
        self._forced_reason = reason
        closed = self._close_streamer_ws()
        logger.warning(
            "Schwab streamer: reconnect requested reason=%s socket_closed=%s",
            reason, closed,
        )
        return closed

    def stream_health(self) -> dict:
        """Cheap, lock-free snapshot for the watchdog and operators.

        ``bar_silence_s`` is seconds since the newest of: supervisor start,
        latest LOGIN, latest delivered bar — so a socket that is up but
        delivering nothing, or a stream that never reconnects, both grow it.
        """
        now = time.monotonic()

        def age(t: Optional[float]) -> Optional[float]:
            return None if t is None else round(now - t, 1)

        anchors = [t for t in (self._started_mono, self._login_mono, self._last_bar_mono) if t is not None]
        thread = self._streamer_thread
        return {
            "running": bool(self._streamer_started and thread is not None and thread.is_alive()),
            "connected": self._streamer_ready.is_set(),
            "bar_silence_s": age(max(anchors)) if anchors else None,
            "last_frame_age_s": age(self._last_frame_mono),
            "last_bar_age_s": age(self._last_bar_mono),
            "reconnect_count": self._reconnect_count,
            "last_exit_reason": self._last_exit_reason,
            "last_exit_at": self._last_exit_at.isoformat() if self._last_exit_at else None,
        }

    def start_stream(self) -> None:
        """Start the supervised Schwab Streamer WebSocket (connect, LOGIN, reconnect). Called after subscribe_bars or explicitly."""
        thread = self._streamer_thread
        if self._streamer_started and thread is not None and thread.is_alive():
            logger.debug("Schwab stream already started")
            return
        if self._streamer_started:
            logger.error("Schwab streamer: supervisor thread is not alive while started; restarting it")
        self._stop_evt.clear()
        self._streamer_ready.clear()
        self._streamer_started = True
        self._started_mono = time.monotonic()
        self._streamer_thread = threading.Thread(
            target=self._streamer_thread_target, name="schwab-streamer", daemon=True,
        )
        self._streamer_thread.start()
        logger.info("Schwab streamer thread started")

    def stop_stream(self) -> None:
        """Stop the Streamer connection and its supervisor (no reconnect)."""
        self._streamer_started = False
        self._stop_evt.set()
        self._enqueue_streamer_cmd({"_type": "STOP"})
        self._close_streamer_ws()
        self._streamer_thread = None
        logger.info("Schwab stream stopped")

    def subscribe_bars(self, callback: Callable, tickers: list[str]) -> None:
        """Subscribe to CHART_EQUITY and/or CHART_FUTURES for given tickers. Starts stream and sends SUBS after LOGIN."""
        try:
            self._main_loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.error("Schwab subscribe_bars: no event loop; call from async context")
            return
        self._bar_callback = callback
        new_tickers = [t.strip().upper() for t in tickers if (t or "").strip()]
        self._subscribed_tickers = list(dict.fromkeys(self._subscribed_tickers + new_tickers))
        logger.info("Schwab subscribing to bars for %s", self._subscribed_tickers)
        self.start_stream()
        # If streamer is already logged in, send SUBS immediately for the requested tickers.
        if self._streamer_ready.is_set():
            # Resend the FULL accumulated set, not just `new_tickers`. SUBS
            # replaces a service's subscription, so sending only the delta
            # would silently unsubscribe everything added before it.
            eq_sub, fu_sub = self.partition_chart_stream_keys(self._subscribed_tickers)
            for req in self.build_chart_subscribe_requests(
                SERVICE_CHART_EQUITY, eq_sub, CHART_EQUITY_FIELDS, first_request_id=2
            ):
                self._enqueue_streamer_cmd(req)
            for req in self.build_chart_subscribe_requests(
                SERVICE_CHART_FUTURES, fu_sub, CHART_FUTURES_FIELDS, first_request_id=100
            ):
                self._enqueue_streamer_cmd(req)

    def unsubscribe_bars(self, tickers: list[str]) -> None:
        """Unsubscribe from bar updates for given tickers (correct chart service per symbol)."""
        norm = [t.strip().upper() for t in tickers if (t or "").strip()]
        for t in norm:
            if t in self._subscribed_tickers:
                self._subscribed_tickers.remove(t)
        logger.info("Schwab unsubscribed from %s; remaining %s", norm, self._subscribed_tickers)
        if self._streamer_ready.is_set() and norm:
            eq_u, fu_u = self.partition_chart_stream_keys(norm)
            if eq_u:
                self._enqueue_streamer_cmd(
                    {
                        "service": SERVICE_CHART_EQUITY,
                        "command": "UNSUBS",
                        "parameters": {"keys": ",".join(eq_u)},
                    }
                )
            if fu_u:
                self._enqueue_streamer_cmd(
                    {
                        "service": SERVICE_CHART_FUTURES,
                        "command": "UNSUBS",
                        "parameters": {"keys": ",".join(fu_u)},
                    }
                )

    async def historical_df(
        self,
        symbol: str,
        start: datetime,
        end: datetime,
        timeframe: str = "1Min",
    ) -> pd.DataFrame:
        """Fetch historical bars from Market Data API price history endpoint."""
        tf_lower = (timeframe or "").lower()
        if tf_lower in ("1min", "1m"):
            period_type, freq_type, freq = "day", "minute", 1
        elif tf_lower in ("5min", "5m"):
            period_type, freq_type, freq = "day", "minute", 5
        elif tf_lower in ("15min", "15m"):
            period_type, freq_type, freq = "day", "minute", 15
        elif tf_lower in ("30min", "30m"):
            period_type, freq_type, freq = "day", "minute", 30
        elif tf_lower in ("1d", "1day", "day", "daily"):
            period_type, freq_type, freq = "month", "daily", 1
        elif tf_lower in ("1w", "1week", "week", "weekly"):
            period_type, freq_type, freq = "year", "weekly", 1
        elif tf_lower in ("1mo", "1month", "month", "monthly"):
            period_type, freq_type, freq = "year", "monthly", 1
        else:
            period_type, freq_type, freq = "day", "minute", 1

        start_ms = int(start.timestamp() * 1000) if start.tzinfo else int(start.replace(tzinfo=timezone.utc).timestamp() * 1000)
        end_ms = int(end.timestamp() * 1000) if end.tzinfo else int(end.replace(tzinfo=timezone.utc).timestamp() * 1000)

        # `period` is intentionally OMITTED.
        #
        # Per the Schwab market_data_api docs (see docs/reference/schwab-api/market_data_api.md):
        #     "If not specified startDate will be (endDate - period) ..."
        # — `period` exists only to derive a default `startDate` when one
        # isn't passed. We always pass `startDate` AND `endDate` explicitly,
        # so `period` is purely advisory.
        #
        # Empirically (Phase 2 backfill, 2026-04-03), passing both `period`
        # and an explicit window can return HTTP 400 with
        # "Enddate ... is before startDate" — an internal date-math conflict
        # in Schwab's server when reconciling the two. Tracked in
        # docs/ISSUES.md::schwab-pricehistory-period-window-conflict.
        # Dropping `period` entirely cured the gap.
        params = {
            "symbol": symbol.upper(),
            "periodType": period_type,
            "frequencyType": freq_type,
            "frequency": freq,
            "startDate": start_ms,
            "endDate": end_ms,
            "needExtendedHoursData": "true",
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

    @staticmethod
    def _normalize_instrument(it: dict) -> dict:
        """Provider-agnostic shape used by /api/instruments/search."""
        return {
            "symbol": (it.get("symbol") or "").upper(),
            "description": it.get("description") or "",
            "exchange": it.get("exchange") or "",
            "asset_type": it.get("assetType") or it.get("type") or "",
        }

    @staticmethod
    def _to_prefix_regex(query: str) -> str:
        """
        Build a safe prefix regex for Schwab's `symbol-regex` projection.
        Schwab equity/ETF tickers are alphanumeric only, so we strip everything
        else - including `.` which is a regex metachar - to make injection
        impossible. The result is anchored at start with a trailing `.*` so
        'NVD' matches NVDA, NVDS, NVDX, etc.

        Futures use a leading ``/`` (e.g. ``/MNQM26``). For queries that start
        with ``/`` we keep the slash and only allow alphanumerics after it so
        ``/mnq`` becomes ``^/MNQ.*``.
        """
        q = (query or "").strip()
        if q.startswith("/"):
            tail = "".join(ch for ch in q[1:] if ch.isalnum())
            if not tail:
                return ""
            return f"^/{tail.upper()}.*"
        cleaned = "".join(ch for ch in query if ch.isalnum())
        if not cleaned:
            return ""
        return f"^{cleaned.upper()}.*"

    # Relevance weights for autocomplete ranking. Higher = pushed to the top.
    # Tuned for retail-trader expectations: typing 'apple' should surface AAPL
    # before mutual funds like APPLESEED INVESTOR.
    _ASSET_TYPE_WEIGHTS = {
        "EQUITY": 30,
        "ETF": 25,
        "INDEX": 20,
        "MUTUAL_FUND": 5,
        "FOREX": 5,
        "FUTURE": 5,
        "OPTION": 0,
    }

    @classmethod
    def _score_instrument(cls, inst: dict, query_upper: str) -> int:
        """
        Rank an instrument against a search query. Pure function; tested
        directly. Higher score = more relevant.
        """
        sym = inst.get("symbol", "")
        desc = (inst.get("description") or "").upper()
        score = 0

        if sym == query_upper:
            score += 200          # exact ticker match wins everything
        elif sym.startswith(query_upper):
            score += 100          # ticker-prefix match (NVD -> NVDA, NVDS)
        elif query_upper in desc:
            score += 30           # description substring (apple -> AAPL)

        # Futures autocomplete: user types "/mnq" — boost matching contracts.
        if query_upper.startswith("/") and sym.startswith("/"):
            score += 55
        elif query_upper.startswith("/") and inst.get("asset_type") == "FUTURE":
            score += 20

        # Bias toward retail-friendly instrument types.
        score += cls._ASSET_TYPE_WEIGHTS.get(inst.get("asset_type", ""), 0)

        # Shorter symbols tend to be more popular (AAPL vs AAPLW). Subtract
        # a small penalty so ties break in favor of shorter tickers.
        score -= len(sym)

        return score

    async def search_instruments(self, query: str, *, limit: int = 10) -> list[dict]:
        """
        Symbol autocomplete via Schwab `/instruments`. Strategy:
          1. `symbol-regex` matches by ticker prefix (covers 'NVD' -> NVDA).
          2. `desc-search` matches by company name (covers 'apple' -> AAPL).
             Skipped for 1-char queries (yields thousands of unranked hits).
          3. Run (1) and (2) in parallel via asyncio.gather so total latency
             is max(call1, call2) instead of call1 + call2 — typically ~half
             the wall-clock for 2+ char queries.
          4. Merge, dedupe by symbol, then re-rank by relevance so EQUITIES
             with exact / prefix matches float to the top BEFORE applying
             `limit`. Without re-ranking, Schwab returns alphabetical lists
             where AAPL is buried behind APPLESEED mutual funds.

        Returns `[]` on any provider error so the UI degrades gracefully.
        """
        q = (query or "").strip()
        if not q or limit <= 0:
            return []
        q_upper = q.upper()

        regex = self._to_prefix_regex(q)
        coros: list[asyncio.Future] = []
        labels: list[str] = []
        if regex:
            coros.append(self._market_data_get(
                INSTRUMENTS_PATH,
                {"symbol": regex, "projection": "symbol-regex"},
            ))
            labels.append("symbol-regex")
        if len(q) >= 2:
            coros.append(self._market_data_get(
                INSTRUMENTS_PATH,
                {"symbol": q, "projection": "desc-search"},
            ))
            labels.append("desc-search")

        merged: dict[str, dict] = {}
        if coros:
            # gather with return_exceptions so one failure doesn't sink
            # the other — autocomplete needs at most one source to work.
            results = await asyncio.gather(*coros, return_exceptions=True)
            for label, result in zip(labels, results):
                if isinstance(result, Exception):
                    logger.debug("Schwab %s search failed for %r: %s", label, q, result)
                    continue
                for it in (result or {}).get("instruments", []) or []:
                    norm = self._normalize_instrument(it)
                    if norm["symbol"]:
                        merged.setdefault(norm["symbol"], norm)

        # Rank, then cap. Sort key:
        #   1. higher score first
        #   2. shorter description as a tiebreaker (canonical companies tend to
        #      have terser descriptions: "APPLE INC" beats "APPLE ISPORTS GROUP")
        #   3. alphabetical symbol order as a final stable tiebreaker
        ranked = sorted(
            merged.values(),
            key=lambda r: (
                -self._score_instrument(r, q_upper),
                len(r.get("description") or ""),
                r["symbol"],
            ),
        )
        return ranked[:limit]

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

    async def get_transactions(
        self,
        account_number: str,
        *,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
        types: Optional[str] = None,
    ) -> dict:
        """
        GET /accounts/{accountNumber}/transactions. Schwab REQUIRES `startDate`
        and `endDate` as ISO-8601 ZonedDateTime strings; defaults here cover
        the past 365 days (the max window Schwab allows per call).

        `types` is an optional comma-separated filter:
            TRADE, RECEIVE_AND_DELIVER, DIVIDEND_OR_INTEREST,
            ACH_RECEIPT, ACH_DISBURSEMENT, CASH_RECEIPT, CASH_DISBURSEMENT,
            ELECTRONIC_FUND, WIRE_OUT, WIRE_IN, JOURNAL,
            MEMORANDUM, MARGIN_CALL, MONEY_MARKET, SMA_ADJUSTMENT
        """
        if end is None:
            end = datetime.now(timezone.utc)
        if start is None:
            start = end - timedelta(days=365)
        path = f"/accounts/{account_number}/transactions"
        params = {
            # Schwab wants `2024-05-12T00:00:00.000Z` style timestamps.
            "startDate": start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "endDate": end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
        }
        if types:
            params["types"] = types
        return await self._trader_get(path, params=params)
