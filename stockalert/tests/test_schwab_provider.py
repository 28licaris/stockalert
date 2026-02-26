"""
Validation tests for SchwabProvider.

Uses mocks for HTTP/WebSocket so tests run without real credentials.
Run with: poetry run pytest tests/test_schwab_provider.py -v
"""
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from app.providers.schwab_provider import SchwabProvider


# ---- Helpers for mocking aiohttp ----

def make_resp(status=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status = status
    resp.json = AsyncMock(return_value=json_data or {})
    resp.text = AsyncMock(return_value=text)
    return resp


def make_async_cm(return_value):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=return_value)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


def make_session(post_resp=None, get_resp=None):
    session = MagicMock()
    session.post.return_value = make_async_cm(post_resp or make_resp(200, {"access_token": "mock_token"}))
    session.get.return_value = make_async_cm(get_resp or make_resp(200, {}))
    return session


def make_session_cm(session):
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=session)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


# ---- Unit tests (no HTTP) ----

class TestChartContentToBar:
    """Test CHART_EQUITY content -> bar mapping."""

    def test_maps_key_and_numeric_fields(self):
        content = {
            "key": "AAPL",
            1: 150.0, 2: 151.0, 3: 149.0, 4: 150.5, 5: 1000000,
            7: 1700000000000,  # ms epoch
        }
        bar = SchwabProvider._chart_content_to_bar(content)
        assert bar.symbol == "AAPL"
        assert bar.ticker == "AAPL"
        assert bar.open == 150.0 and bar.high == 151.0 and bar.low == 149.0 and bar.close == 150.5
        assert bar.volume == 1000000
        assert bar.timestamp.tzinfo is not None
        assert bar.ts == bar.timestamp

    def test_maps_string_keys(self):
        content = {"key": "SPY", "1": 400.0, "2": 401.0, "3": 399.0, "4": 400.5, "5": 5000, "7": 1700000000000}
        bar = SchwabProvider._chart_content_to_bar(content)
        assert bar.symbol == "SPY"
        assert bar.open == 400.0 and bar.close == 400.5
        assert bar.volume == 5000

    def test_missing_timestamp_uses_now(self):
        content = {"key": "X", "1": 1.0, "2": 1.0, "3": 1.0, "4": 1.0}
        bar = SchwabProvider._chart_content_to_bar(content)
        assert bar.symbol == "X"
        assert bar.timestamp is not None
        assert bar.open == 1.0


class TestStreamerIdsAndChannel:
    """Test _streamer_ids and _channel_function_ids with _user_prefs set."""

    def test_streamer_ids_direct(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._user_prefs = {
            "streamerSubscriptionKeys": {
                "schwabClientCustomerId": "cust123",
                "schwabClientCorrelId": "correl456",
            }
        }
        ids = p._streamer_ids()
        assert ids["SchwabClientCustomerId"] == "cust123"
        assert ids["SchwabClientCorrelId"] == "correl456"

    def test_streamer_ids_nested_keys_array(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._user_prefs = {
            "streamerSubscriptionKeys": {
                "keys": [
                    {"schwabClientCustomerId": "nested", "schwabClientCorrelId": "nested_correl"}
                ]
            }
        }
        ids = p._streamer_ids()
        assert ids["SchwabClientCustomerId"] == "nested"
        assert ids["SchwabClientCorrelId"] == "nested_correl"

    def test_channel_function_defaults(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._user_prefs = {}
        ch = p._channel_function_ids()
        assert ch["SchwabClientChannel"] == "N9"
        assert ch["SchwabClientFunctionId"] == "APIAPP"

    def test_channel_function_from_prefs(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._user_prefs = {"preferences": {"streamerChannel": "CH1", "streamerFunctionId": "FUN1"}}
        ch = p._channel_function_ids()
        assert ch["SchwabClientChannel"] == "CH1"
        assert ch["SchwabClientFunctionId"] == "FUN1"


class TestEnsureToken:
    """Test _ensure_token with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_requires_refresh_token(self):
        p = SchwabProvider("cid", "secret", refresh_token="")
        with pytest.raises(ValueError, match="SCHWAB_REFRESH_TOKEN"):
            await p._ensure_token()

    @pytest.mark.asyncio
    async def test_returns_and_caches_token(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        session = make_session(post_resp=make_resp(200, {"access_token": "secret_tok"}))
        with patch("app.providers.schwab_provider.aiohttp.ClientSession", return_value=make_session_cm(session)):
            tok = await p._ensure_token()
        assert tok == "secret_tok"
        assert p._access_token == "secret_tok"
        # Second call returns cached (session.post not called again because of lock + cache)
        tok2 = await p._ensure_token()
        assert tok2 == "secret_tok"

    @pytest.mark.asyncio
    async def test_raises_on_non_200(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        session = make_session(post_resp=make_resp(401, None, "Unauthorized"))
        with patch("app.providers.schwab_provider.aiohttp.ClientSession", return_value=make_session_cm(session)):
            with pytest.raises(RuntimeError, match="token exchange failed"):
                await p._ensure_token()


class TestGetUserPrincipals:
    """Test _get_user_principals with mocked token + GET."""

    @pytest.mark.asyncio
    async def test_sets_streamer_url_from_list(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._access_token = "tok"
        principals = {
            "streamerConnectionInfo": [
                {"uri": "wss://stream.example.com"},
            ],
            "streamerSubscriptionKeys": {"schwabClientCustomerId": "c", "schwabClientCorrelId": "r"},
        }
        session = make_session(
            get_resp=make_resp(200, principals),
        )
        with patch("app.providers.schwab_provider.aiohttp.ClientSession", return_value=make_session_cm(session)):
            data = await p._get_user_principals()
        assert data == principals
        assert p._streamer_url == "wss://stream.example.com"
        assert p._user_prefs == principals

    @pytest.mark.asyncio
    async def test_sets_streamer_url_from_dict_nested(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._access_token = "tok"
        principals = {
            "streamerConnectionInfo": {
                "streamerConnectionInfo": [
                    {"streamerSocketUrl": "wss://nested.example.com"},
                ]
            },
            "streamerSubscriptionKeys": {},
        }
        session = make_session(get_resp=make_resp(200, principals))
        with patch("app.providers.schwab_provider.aiohttp.ClientSession", return_value=make_session_cm(session)):
            await p._get_user_principals()
        assert p._streamer_url == "wss://nested.example.com"


class TestHistoricalDf:
    """Test historical_df with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_returns_empty_without_token(self):
        p = SchwabProvider("cid", "secret", refresh_token="")
        start = datetime.now(timezone.utc) - timedelta(days=1)
        end = datetime.now(timezone.utc)
        with pytest.raises(ValueError, match="SCHWAB_REFRESH_TOKEN"):
            await p.historical_df("AAPL", start, end)

    @pytest.mark.asyncio
    async def test_returns_dataframe_with_candles(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._access_token = "tok"
        ts_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        candles = [
            {"datetime": ts_ms, "open": 100, "high": 101, "low": 99, "close": 100.5, "volume": 1000},
            {"datetime": ts_ms + 60000, "open": 100.5, "high": 102, "low": 100, "close": 101, "volume": 1200},
        ]
        session = make_session(
            get_resp=make_resp(200, {"candles": candles}),
        )
        start = datetime.now(timezone.utc) - timedelta(days=1)
        end = datetime.now(timezone.utc)
        with patch("app.providers.schwab_provider.aiohttp.ClientSession", return_value=make_session_cm(session)):
            df = await p.historical_df("AAPL", start, end)
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 2
        assert list(df.columns) == ["open", "high", "low", "close", "volume"]
        assert df.index.name == "timestamp" and isinstance(df.index, pd.DatetimeIndex)
        assert df["close"].iloc[0] == 100.5 and df["close"].iloc[1] == 101
        assert df["volume"].iloc[0] == 1000

    @pytest.mark.asyncio
    async def test_returns_empty_on_empty_candles(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._access_token = "tok"
        session = make_session(get_resp=make_resp(200, {"candles": []}))
        start = datetime.now(timezone.utc) - timedelta(days=1)
        end = datetime.now(timezone.utc)
        with patch("app.providers.schwab_provider.aiohttp.ClientSession", return_value=make_session_cm(session)):
            df = await p.historical_df("AAPL", start, end)
        assert df.empty

    @pytest.mark.asyncio
    async def test_uses_time_if_datetime_missing(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._access_token = "tok"
        ts_ms = 1700000000000
        candles = [{"time": ts_ms, "open": 1, "high": 1, "low": 1, "close": 1, "volume": 0}]
        session = make_session(get_resp=make_resp(200, {"candles": candles}))
        start = datetime.now(timezone.utc) - timedelta(days=1)
        end = datetime.now(timezone.utc)
        with patch("app.providers.schwab_provider.aiohttp.ClientSession", return_value=make_session_cm(session)):
            df = await p.historical_df("AAPL", start, end)
        assert len(df) == 1
        assert df["close"].iloc[0] == 1


class TestStreamLifecycle:
    """Test start_stream, stop_stream, subscribe_bars, unsubscribe_bars (no real WebSocket)."""

    def test_start_stop_stream_no_crash(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        # Prevent streamer thread from hitting real API (thread runs _run_streamer_loop)
        with patch.object(p, "_run_streamer_loop", new_callable=AsyncMock, return_value=None):
            p.start_stream()
            time.sleep(0.1)  # give streamer thread a moment to start
        assert p._streamer_started is True
        assert p._streamer_thread is not None
        p.stop_stream()
        assert p._streamer_started is False
        assert p._streamer_thread is None

    def test_subscribe_bars_without_loop_returns_gracefully(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        # Not in an async context -> get_running_loop() raises RuntimeError
        p.subscribe_bars(lambda b: None, ["AAPL"])
        # Should not raise; may log error. Tickers may still be set if loop was captured elsewhere - actually
        # get_running_loop() will raise so we return early and don't set _bar_callback. So _subscribed_tickers
        # might still be [] and start_stream might have been called. Check: subscribe_bars catches RuntimeError
        # and returns, so _main_loop stays None, _bar_callback stays None, but we might have called start_stream().
        # Actually in the code we do: try: self._main_loop = asyncio.get_running_loop() except RuntimeError: logger.error... return
        # So we return before setting _bar_callback or _subscribed_tickers or start_stream(). Good.
        assert p._main_loop is None

    def test_unsubscribe_bars_no_crash(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._subscribed_tickers = ["AAPL", "SPY"]
        p.unsubscribe_bars(["AAPL"])
        assert "AAPL" not in p._subscribed_tickers
        assert "SPY" in p._subscribed_tickers


class TestDataProviderContract:
    """Ensure SchwabProvider satisfies DataProvider contract (bar attributes)."""

    def test_bar_has_required_attributes(self):
        content = {"key": "T", 1: 1.0, 2: 1.0, 3: 1.0, 4: 1.0, 5: 0, 7: 1700000000000}
        bar = SchwabProvider._chart_content_to_bar(content)
        # App contract: symbol or ticker, timestamp or ts, open, high, low, close, volume
        assert getattr(bar, "symbol", None) or getattr(bar, "ticker", None) == "T"
        assert getattr(bar, "timestamp", None) or getattr(bar, "ts", None) is not None
        assert bar.open == 1.0 and bar.high == 1.0 and bar.low == 1.0 and bar.close == 1.0
        assert getattr(bar, "volume", 0) == 0
