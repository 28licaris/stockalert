"""
Validation tests for SchwabProvider.

Uses mocks for HTTP/WebSocket so tests run without real credentials.
Run with: poetry run pytest app/providers/tests/test_schwab_provider.py -v
"""
import asyncio
import time
from datetime import datetime, timezone, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from app.providers.schwab_provider import (
    MARKET_DATA_BASE,
    PRICE_HISTORY_PATH,
    SchwabProvider,
)


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

    # Schwab CHART_EQUITY empirical field map (see CHART_EQUITY_FIELDS in
    # app/providers/schwab_provider.py): 0=key, 1=Sequence (unused),
    # 2=Open, 3=High, 4=Low, 5=Close, 6=Volume, 7=ChartTime(ms).
    # Schwab's published table is wrong — these tests follow what the
    # live stream actually emits.

    def test_maps_key_and_numeric_fields(self):
        content = {
            "key": "AAPL",
            2: 150.0, 3: 151.0, 4: 149.0, 5: 150.5, 6: 1000000,
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
        content = {"key": "SPY", "2": 400.0, "3": 401.0, "4": 399.0, "5": 400.5, "6": 5000, "7": 1700000000000}
        bar = SchwabProvider._chart_content_to_bar(content)
        assert bar.symbol == "SPY"
        assert bar.open == 400.0 and bar.close == 400.5
        assert bar.volume == 5000

    def test_missing_timestamp_uses_now(self):
        content = {"key": "X", "2": 1.0, "3": 1.0, "4": 1.0, "5": 1.0}
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

    def test_streamer_ids_from_streamer_info(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._user_prefs = {
            "streamerInfo": [
                {"schwabClientCustomerId": "si_cust", "schwabClientCorrelId": "si_correl"}
            ]
        }
        ids = p._streamer_ids()
        assert ids["SchwabClientCustomerId"] == "si_cust"
        assert ids["SchwabClientCorrelId"] == "si_correl"

    def test_channel_function_from_streamer_info(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._user_prefs = {
            "streamerInfo": [
                {"schwabClientChannel": "S9", "schwabClientFunctionId": "TICKET"}
            ]
        }
        ch = p._channel_function_ids()
        assert ch["SchwabClientChannel"] == "S9"
        assert ch["SchwabClientFunctionId"] == "TICKET"


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
        assert session.post.call_count == 1

    @pytest.mark.asyncio
    async def test_concurrent_ensure_token_single_post(self):
        """Parallel awaits must not each hit the token endpoint (refresh rotation / log noise)."""
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        session = make_session(post_resp=make_resp(200, {"access_token": "secret_tok"}))
        with patch("app.providers.schwab_provider.aiohttp.ClientSession", return_value=make_session_cm(session)):
            out = await asyncio.gather(
                p._ensure_token(),
                p._ensure_token(),
                p._ensure_token(),
            )
        assert out == ["secret_tok", "secret_tok", "secret_tok"]
        assert session.post.call_count == 1

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
    async def test_sets_streamer_url_from_streamer_info_uri_fallback(self):
        """Production parser tries streamerSocketUrl first, then `uri`, then
        `websocketUrl` (see `_get_user_principals` in
        app/providers/schwab_provider.py). Covers the `uri` fallback path —
        the streamerSocketUrl happy path is exercised by
        test_sets_streamer_url_from_streamer_info below."""
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._access_token = "tok"
        principals = {
            "streamerInfo": [
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
    async def test_sets_streamer_url_from_streamer_info(self):
        """Account Access API returns streamerInfo array (streamerSocketUrl, schwabClientCustomerId, etc.)."""
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._access_token = "tok"
        principals = {
            "streamerInfo": [
                {
                    "streamerSocketUrl": "wss://streamer.schwab.com",
                    "schwabClientCustomerId": "cust99",
                    "schwabClientCorrelId": "correl99",
                    "schwabClientChannel": "N9",
                    "schwabClientFunctionId": "APIAPP",
                }
            ],
        }
        session = make_session(get_resp=make_resp(200, principals))
        with patch("app.providers.schwab_provider.aiohttp.ClientSession", return_value=make_session_cm(session)):
            await p._get_user_principals()
        assert p._streamer_url == "wss://streamer.schwab.com"
        ids = p._streamer_ids()
        assert ids["SchwabClientCustomerId"] == "cust99"
        assert ids["SchwabClientCorrelId"] == "correl99"
        ch = p._channel_function_ids()
        assert ch["SchwabClientChannel"] == "N9"
        assert ch["SchwabClientFunctionId"] == "APIAPP"


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

    @pytest.mark.asyncio
    async def test_uses_market_data_base_url_and_symbol_param(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._access_token = "tok"
        session = make_session(get_resp=make_resp(200, {"candles": []}))
        start = datetime(2025, 6, 1, 10, 0, 0, tzinfo=timezone.utc)
        end = datetime(2025, 6, 1, 16, 0, 0, tzinfo=timezone.utc)
        with patch("app.providers.schwab_provider.aiohttp.ClientSession", return_value=make_session_cm(session)):
            await p.historical_df("AAPL", start, end)
        session.get.assert_called_once()
        call_args, call_kwargs = session.get.call_args
        (url,) = call_args
        assert MARKET_DATA_BASE in url
        assert PRICE_HISTORY_PATH in url
        params = call_kwargs.get("params", {})
        assert params.get("symbol") == "AAPL"
        # Explicit window is sent; `period` must NOT be sent — see
        # docs/ISSUES.md::schwab-pricehistory-period-window-conflict.
        # Passing both `period` and an explicit startDate/endDate can return
        # HTTP 400 "Enddate ... is before startDate" (Schwab quirk).
        assert "period" not in params
        assert "startDate" in params and "endDate" in params

    @pytest.mark.asyncio
    async def test_single_day_window_does_not_send_period(self):
        """Regression for the 2026-04-03 Phase 2 backfill gap.

        A 1-day Schwab pricehistory window used to fail with HTTP 400 when
        the provider sent `period=1` alongside explicit `startDate`/`endDate`.
        Pin that we never send `period` even on a degenerate same-day window.
        """
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._access_token = "tok"
        session = make_session(get_resp=make_resp(200, {"candles": []}))
        # Same calendar day, 1-minute window — what the backfill CLI sends
        # when invoked with `--start 2026-04-03 --end 2026-04-03`.
        start = datetime(2026, 4, 3, 13, 30, 0, tzinfo=timezone.utc)
        end = datetime(2026, 4, 3, 20, 0, 0, tzinfo=timezone.utc)
        with patch("app.providers.schwab_provider.aiohttp.ClientSession", return_value=make_session_cm(session)):
            await p.historical_df("AAPL", start, end, timeframe="1Min")
        params = session.get.call_args.kwargs.get("params", {})
        assert "period" not in params
        assert params["periodType"] == "day"
        assert params["frequencyType"] == "minute"
        assert params["frequency"] == 1


class TestMarketDataGet:
    """Test _market_data_get with mocked HTTP."""

    @pytest.mark.asyncio
    async def test_returns_json_on_200(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._access_token = "tok"
        session = make_session(get_resp=make_resp(200, {"key": "value"}))
        with patch("app.providers.schwab_provider.aiohttp.ClientSession", return_value=make_session_cm(session)):
            out = await p._market_data_get("/quotes", {"symbols": "AAPL"})
        assert out == {"key": "value"}
        session.get.assert_called_once()
        call_args, call_kwargs = session.get.call_args
        assert MARKET_DATA_BASE in call_args[0]
        assert call_kwargs["params"] == {"symbols": "AAPL"}

    @pytest.mark.asyncio
    async def test_serializes_bool_query_params(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._access_token = "tok"
        session = make_session(get_resp=make_resp(200, {"key": "value"}))
        with patch("app.providers.schwab_provider.aiohttp.ClientSession", return_value=make_session_cm(session)):
            await p._market_data_get(
                "/chains",
                {"symbol": "AAPL", "includeUnderlyingQuote": True, "unused": None},
            )
        params = session.get.call_args.kwargs["params"]
        assert params == {"symbol": "AAPL", "includeUnderlyingQuote": "true"}

    @pytest.mark.asyncio
    async def test_returns_empty_dict_on_non_200(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._access_token = "tok"
        session = make_session(get_resp=make_resp(404, None, "Not Found"))
        with patch("app.providers.schwab_provider.aiohttp.ClientSession", return_value=make_session_cm(session)):
            out = await p._market_data_get("/quotes", {"symbols": "X"})
        assert out == {}

    @pytest.mark.asyncio
    async def test_clears_token_and_retries_on_401(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._access_token = "tok"
        first_get = make_resp(401, None, "Unauthorized")
        second_get = make_resp(200, {"data": 1})
        session = MagicMock()
        session.get = MagicMock(
            side_effect=[
                make_async_cm(first_get),
                make_async_cm(second_get),
            ]
        )
        session.post = MagicMock(return_value=make_async_cm(make_resp(200, {"access_token": "new_tok"})))
        with patch("app.providers.schwab_provider.aiohttp.ClientSession", return_value=make_session_cm(session)):
            out = await p._market_data_get("/quotes", {})
        assert out == {"data": 1}
        assert session.get.call_count == 2
        assert p._access_token == "new_tok"


class TestMarketDataMethods:
    """Test get_quotes, get_quote, get_option_chains, etc. call _market_data_get with correct path/params."""

    @pytest.mark.asyncio
    async def test_get_quotes(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        with patch.object(p, "_market_data_get", new_callable=AsyncMock, return_value={"AAPL": {}}) as mock_get:
            out = await p.get_quotes(["AAPL", "SPY"])
        assert out == {"AAPL": {}}
        mock_get.assert_called_once()
        args = mock_get.call_args[0]
        assert args[0] == "/quotes"
        assert args[1]["symbols"] == "AAPL,SPY"

    @pytest.mark.asyncio
    async def test_get_quote(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        with patch.object(p, "_market_data_get", new_callable=AsyncMock, return_value={"lastPrice": 150}) as mock_get:
            out = await p.get_quote("AAPL")
        assert out == {"lastPrice": 150}
        mock_get.assert_called_once()
        path = mock_get.call_args[0][0]
        assert "AAPL" in path and "quotes" in path

    @pytest.mark.asyncio
    async def test_get_option_chains(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        with patch.object(p, "_market_data_get", new_callable=AsyncMock, return_value={}) as mock_get:
            await p.get_option_chains("AAPL", strikeCount=5)
        mock_get.assert_called_once()
        args = mock_get.call_args[0]
        assert args[0] == "/chains"
        assert args[1]["symbol"] == "AAPL"
        assert args[1]["strikeCount"] == 5

    @pytest.mark.asyncio
    async def test_get_expiration_chain(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        with patch.object(p, "_market_data_get", new_callable=AsyncMock, return_value={}) as mock_get:
            await p.get_expiration_chain("SPY")
        mock_get.assert_called_once()
        args = mock_get.call_args[0]
        assert args[0] == "/expirationchain"
        assert args[1]["symbol"] == "SPY"

    @pytest.mark.asyncio
    async def test_get_movers(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        with patch.object(p, "_market_data_get", new_callable=AsyncMock, return_value=[]) as mock_get:
            await p.get_movers("$SPX")
        mock_get.assert_called_once()
        assert "$SPX" in mock_get.call_args[0][0]

    @pytest.mark.asyncio
    async def test_get_market_hours_all(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        with patch.object(p, "_market_data_get", new_callable=AsyncMock, return_value={}) as mock_get:
            await p.get_market_hours()
        mock_get.assert_called_once()
        assert mock_get.call_args[0][0] == "/markets"
        assert mock_get.call_args[0][1] is None

    @pytest.mark.asyncio
    async def test_get_market_hours_single(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        with patch.object(p, "_market_data_get", new_callable=AsyncMock, return_value={}) as mock_get:
            await p.get_market_hours("equity")
        mock_get.assert_called_once()
        path = mock_get.call_args[0][0]
        assert "markets" in path and "equity" in path

    @pytest.mark.asyncio
    async def test_get_instruments(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        with patch.object(p, "_market_data_get", new_callable=AsyncMock, return_value={}) as mock_get:
            await p.get_instruments(["AAPL", "MSFT"], "symbol-search")
        mock_get.assert_called_once()
        args = mock_get.call_args[0]
        assert args[0] == "/instruments"
        assert args[1]["symbol"] == "AAPL,MSFT"
        assert args[1]["projection"] == "symbol-search"

    @pytest.mark.asyncio
    async def test_get_instrument(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        with patch.object(p, "_market_data_get", new_callable=AsyncMock, return_value={"cusip": "037833100"}) as mock_get:
            out = await p.get_instrument("037833100")
        assert out == {"cusip": "037833100"}
        mock_get.assert_called_once()
        path = mock_get.call_args[0][0]
        assert "instruments" in path and "037833100" in path


class TestTraderApiMethods:
    """Test Trader API (Account Access) data methods call _trader_get with correct path."""

    @pytest.mark.asyncio
    async def test_get_account_numbers(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        with patch.object(p, "_trader_get", new_callable=AsyncMock, return_value=[{"accountNumber": "123", "hashValue": "abc"}]) as mock_get:
            out = await p.get_account_numbers()
        assert out == [{"accountNumber": "123", "hashValue": "abc"}]
        mock_get.assert_called_once_with("/accounts/accountNumbers")

    @pytest.mark.asyncio
    async def test_get_accounts(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        with patch.object(p, "_trader_get", new_callable=AsyncMock, return_value={"accounts": []}) as mock_get:
            out = await p.get_accounts()
        assert out == {"accounts": []}
        mock_get.assert_called_once_with("/accounts")

    @pytest.mark.asyncio
    async def test_get_account(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        with patch.object(p, "_trader_get", new_callable=AsyncMock, return_value={"securitiesAccount": {}}) as mock_get:
            out = await p.get_account("encrypted_hash_123")
        assert out == {"securitiesAccount": {}}
        mock_get.assert_called_once_with("/accounts/encrypted_hash_123")

    @pytest.mark.asyncio
    async def test_get_orders_with_account(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        with patch.object(p, "_trader_get", new_callable=AsyncMock, return_value={"orders": []}) as mock_get:
            out = await p.get_orders("123")
        assert out == {"orders": []}
        mock_get.assert_called_once_with("/accounts/123/orders")

    @pytest.mark.asyncio
    async def test_get_orders_all_accounts(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        with patch.object(p, "_trader_get", new_callable=AsyncMock, return_value={"orders": []}) as mock_get:
            out = await p.get_orders(None)
        assert out == {"orders": []}
        mock_get.assert_called_once_with("/orders")

    @pytest.mark.asyncio
    async def test_get_transactions(self):
        """Schwab requires startDate/endDate; we auto-default to last 365d."""
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        with patch.object(p, "_trader_get", new_callable=AsyncMock, return_value={"transactions": []}) as mock_get:
            out = await p.get_transactions("456")
        assert out == {"transactions": []}
        mock_get.assert_called_once()
        args, kwargs = mock_get.call_args
        assert args[0] == "/accounts/456/transactions"
        params = kwargs.get("params") or (args[1] if len(args) > 1 else {})
        assert "startDate" in params and "endDate" in params
        assert "types" not in params

    @pytest.mark.asyncio
    async def test_get_transactions_passes_types_filter(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        with patch.object(p, "_trader_get", new_callable=AsyncMock, return_value=[]) as mock_get:
            await p.get_transactions("456", types="TRADE")
        _, kwargs = mock_get.call_args
        assert kwargs["params"]["types"] == "TRADE"


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
        # Empirical CHART_EQUITY field map: 2=Open, 3=High, 4=Low, 5=Close, 6=Volume.
        content = {"key": "T", 2: 1.0, 3: 1.0, 4: 1.0, 5: 1.0, 6: 0, 7: 1700000000000}
        bar = SchwabProvider._chart_content_to_bar(content)
        # App contract: symbol or ticker, timestamp or ts, open, high, low, close, volume
        assert getattr(bar, "symbol", None) or getattr(bar, "ticker", None) == "T"
        assert getattr(bar, "timestamp", None) or getattr(bar, "ts", None) is not None
        assert bar.open == 1.0 and bar.high == 1.0 and bar.low == 1.0 and bar.close == 1.0
        assert getattr(bar, "volume", 0) == 0


# ---- Streamer supervision (reconnect, idle timeout, close logging) ----

import json  # noqa: E402
import logging  # noqa: E402
from types import SimpleNamespace  # noqa: E402

import aiohttp  # noqa: E402

from app.providers import schwab_provider as sp  # noqa: E402
from app.providers.schwab_provider import reconnect_delay  # noqa: E402


def _tunables(**over):
    base = {"base": 0.01, "cap": 0.02, "healthy_after": 999.0, "idle": 0.05}
    base.update(over)
    return staticmethod(lambda: dict(base))


def _wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return False


class TestReconnectDelay:
    def test_doubles_then_caps(self):
        mid = lambda: 0.5  # noqa: E731 — jitter factor exactly 1.0
        got = [reconnect_delay(a, base=2, cap=300, rand=mid) for a in range(10)]
        assert got[:4] == [2, 4, 8, 16]
        assert got[-1] == 300
        assert all(0 <= d <= 300 for d in got)

    def test_jitter_is_bounded_and_never_exceeds_cap(self):
        lo = reconnect_delay(3, base=2, cap=300, jitter=0.2, rand=lambda: 0.0)
        hi = reconnect_delay(3, base=2, cap=300, jitter=0.2, rand=lambda: 1.0)
        assert lo == pytest.approx(16 * 0.8)
        assert hi == pytest.approx(16 * 1.2)
        # at the cap, +jitter is clamped back to the cap
        assert reconnect_delay(20, base=2, cap=300, jitter=0.2, rand=lambda: 1.0) == 300

    def test_negative_attempt_is_treated_as_first(self):
        assert reconnect_delay(-5, base=2, cap=300, rand=lambda: 0.5) == 2


class TestStreamerSupervisor:
    """The thread target reconnects after a session ends; only stop_stream stops it."""

    def test_reconnects_after_each_session_ends(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        run = AsyncMock(side_effect=["ws_close", "idle_timeout", "ws_close", "ws_close", "ws_close"])
        with patch.object(SchwabProvider, "_stream_tunables", _tunables()), \
                patch.object(p, "_run_streamer_loop", run):
            p.start_stream()
            assert _wait_for(lambda: run.await_count >= 3), "supervisor did not reconnect"
            assert p._streamer_started is True  # desired state survives session ends
            assert p._reconnect_count >= 2
            assert p._last_exit_reason in {"ws_close", "idle_timeout"}
            p.stop_stream()

    def test_a_crashing_session_does_not_kill_the_supervisor(self, caplog):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        run = AsyncMock(side_effect=[RuntimeError("boom"), "ws_close", "ws_close", "ws_close"])
        with patch.object(SchwabProvider, "_stream_tunables", _tunables()), \
                patch.object(p, "_run_streamer_loop", run), \
                caplog.at_level(logging.WARNING, logger=sp.logger.name):
            p.start_stream()
            assert _wait_for(lambda: run.await_count >= 2)
            p.stop_stream()
        assert any("session crashed" in r.getMessage() for r in caplog.records)
        assert any("reason=crash" in r.getMessage() for r in caplog.records)

    def test_stop_during_backoff_exits_promptly(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        run = AsyncMock(return_value="ws_close")
        # 30 s backoff: a stop must not wait for it.
        with patch.object(SchwabProvider, "_stream_tunables", _tunables(base=30.0, cap=30.0)), \
                patch.object(p, "_run_streamer_loop", run):
            p.start_stream()
            thread = p._streamer_thread
            assert _wait_for(lambda: run.await_count >= 1)
            time.sleep(0.05)  # now inside the backoff wait
            t0 = time.monotonic()
            p.stop_stream()
            thread.join(timeout=2.0)
        assert not thread.is_alive()
        assert time.monotonic() - t0 < 2.0
        assert run.await_count == 1  # no reconnect after stop

    def test_start_stream_restarts_a_dead_supervisor(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        dead = MagicMock()
        dead.is_alive.return_value = False
        p._streamer_started = True
        p._streamer_thread = dead
        run = AsyncMock(return_value="ws_close")
        with patch.object(SchwabProvider, "_stream_tunables", _tunables(base=30.0, cap=30.0)), \
                patch.object(p, "_run_streamer_loop", run):
            p.start_stream()
            assert p._streamer_thread is not dead
            assert _wait_for(lambda: run.await_count >= 1)
            p.stop_stream()

    def test_stream_health_reports_state(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        assert p.stream_health()["bar_silence_s"] is None
        assert p.stream_health()["running"] is False
        run = AsyncMock(return_value="ws_close")
        with patch.object(SchwabProvider, "_stream_tunables", _tunables(base=30.0, cap=30.0)), \
                patch.object(p, "_run_streamer_loop", run):
            p.start_stream()
            assert _wait_for(lambda: run.await_count >= 1)
            h = p.stream_health()
            p.stop_stream()
        assert h["running"] is True
        assert h["bar_silence_s"] is not None and h["bar_silence_s"] >= 0
        assert h["last_bar_age_s"] is None


class FakeWS:
    """Scripted aiohttp websocket: pops frames; with none left it stays silent."""

    def __init__(self, frames):
        self._frames = list(frames)
        self.closed = False
        self.close_code = None
        self.sent: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        self.closed = True

    async def send_str(self, s):
        self.sent.append(s)

    async def receive(self):
        if self._frames:
            return self._frames.pop(0)
        await asyncio.sleep(3600)  # silence: only the idle timeout can end this

    async def close(self):
        self.closed = True

    def exception(self):
        return None


def _text(payload) -> SimpleNamespace:
    return SimpleNamespace(type=aiohttp.WSMsgType.TEXT, data=json.dumps(payload))


def _close(data=1006) -> SimpleNamespace:
    return SimpleNamespace(type=aiohttp.WSMsgType.CLOSE, data=data)


LOGIN_OK = {"response": [{"service": "ADMIN", "command": "LOGIN", "content": {"code": 0}}]}


def _bar_frame(sym="AAPL"):
    return {"data": [{"service": "CHART_EQUITY", "content": [
        {"key": sym, 2: 1.0, 3: 2.0, 4: 0.5, 5: 1.5, 6: 100, 7: 1700000000000}]}]}


def _fake_session_patch(ws):
    """Patch aiohttp.ClientSession so `ws_connect` yields the scripted `ws`."""
    fake_session = MagicMock()
    fake_session.__aenter__ = AsyncMock(return_value=fake_session)
    fake_session.__aexit__ = AsyncMock(return_value=None)
    fake_session.ws_connect = MagicMock(return_value=ws)
    return patch.object(sp.aiohttp, "ClientSession", MagicMock(return_value=fake_session))


def _provider_with_fake_ws(ws):
    p = SchwabProvider("cid", "secret", refresh_token="rt")
    p._user_prefs = {"streamerSubscriptionKeys": {"schwabClientCustomerId": "c", "schwabClientCorrelId": "r"}}
    p._streamer_url = "wss://example/ws"
    p._ensure_token = AsyncMock(return_value="tok")
    p._get_user_principals = AsyncMock()
    p._streamer_started = True
    return p, _fake_session_patch(ws)


@pytest.mark.asyncio
class TestStreamerSession:
    """`_run_streamer_loop` returns WHY a session ended and logs every close."""

    async def test_close_after_login_returns_ws_close_and_dispatches_bars(self, caplog):
        got = []

        async def cb(bar):
            got.append(bar)

        ws = FakeWS([_text(LOGIN_OK), _text({"notify": [{"heartbeat": "1"}]}), _text(_bar_frame()), _close()])
        p, session_patch = _provider_with_fake_ws(ws)
        p._bar_callback = cb
        p._main_loop = asyncio.get_running_loop()
        with session_patch, patch.object(SchwabProvider, "_stream_tunables", _tunables(idle=5.0)), \
                caplog.at_level(logging.INFO, logger=sp.logger.name):
            reason = await p._run_streamer_loop()
            await asyncio.sleep(0)  # let the scheduled bar callback run
        assert reason == "ws_close"
        assert [b.symbol for b in got] == ["AAPL"]
        assert p._last_bar_mono is not None
        assert p._streamer_ready.is_set() is False  # per-session state cleaned up
        assert p._streamer_started is True          # desired state untouched
        assert any("websocket ended reason=ws_close" in r.getMessage() for r in caplog.records)
        assert any("close_code" in r.getMessage() for r in caplog.records)

    async def test_silent_socket_is_declared_dead_by_idle_timeout(self, caplog):
        ws = FakeWS([_text(LOGIN_OK)])  # then total silence (no heartbeat either)
        p, session_patch = _provider_with_fake_ws(ws)
        with session_patch, patch.object(SchwabProvider, "_stream_tunables", _tunables(idle=0.05)), \
                caplog.at_level(logging.WARNING, logger=sp.logger.name):
            reason = await p._run_streamer_loop()
        assert reason == "idle_timeout"
        assert any("treating the socket as dead" in r.getMessage() for r in caplog.records)

    async def test_close_before_login_is_reported(self):
        ws = FakeWS([_close()])
        p, session_patch = _provider_with_fake_ws(ws)
        with session_patch, patch.object(SchwabProvider, "_stream_tunables", _tunables(idle=5.0)):
            assert await p._run_streamer_loop() == "closed_before_login"

    async def test_login_denied_returns_login_failed(self):
        denied = {"response": [{"service": "ADMIN", "command": "LOGIN", "content": {"code": 3, "msg": "denied"}}]}
        ws = FakeWS([_text(denied)])
        p, session_patch = _provider_with_fake_ws(ws)
        with session_patch, patch.object(SchwabProvider, "_stream_tunables", _tunables(idle=5.0)):
            assert await p._run_streamer_loop() == "login_failed"

    async def test_token_failure_is_a_reason_not_a_silent_return(self):
        p = SchwabProvider("cid", "secret", refresh_token="rt")
        p._ensure_token = AsyncMock(side_effect=RuntimeError("invalid_grant"))
        assert await p._run_streamer_loop() == "token_failed"

    async def test_forced_reconnect_reason_is_reported(self):
        ws = FakeWS([_text(LOGIN_OK), _close()])
        p, session_patch = _provider_with_fake_ws(ws)
        p._forced_reason = "stale_no_bars"
        with session_patch, patch.object(SchwabProvider, "_stream_tunables", _tunables(idle=5.0)):
            assert await p._run_streamer_loop() == "stale_no_bars"
        assert p._forced_reason is None  # consumed

    async def test_non_zero_ack_is_logged_with_its_meaning(self, caplog):
        ack = {"response": [{"service": "CHART_EQUITY", "command": "ADD",
                             "content": {"code": 19, "msg": "limit"}}]}
        ws = FakeWS([_text(LOGIN_OK), _text(ack), _close()])
        p, session_patch = _provider_with_fake_ws(ws)
        with session_patch, patch.object(SchwabProvider, "_stream_tunables", _tunables(idle=5.0)), \
                caplog.at_level(logging.WARNING, logger=sp.logger.name):
            await p._run_streamer_loop()
        assert any("REACHED_SYMBOL_LIMIT" in r.getMessage() and "code=19" in r.getMessage()
                   for r in caplog.records)

    async def test_resubscribes_full_set_on_every_session(self):
        """A reconnect must replay the whole subscription, not start empty."""
        async def cb(bar):  # pragma: no cover - never called
            pass

        p, _ = _provider_with_fake_ws(FakeWS([]))
        p._bar_callback = cb
        p._subscribed_tickers = ["AAPL", "MSFT", "/ES"]
        for _session in range(2):  # two sessions on the same provider
            ws = FakeWS([_text(LOGIN_OK), _close()])
            with _fake_session_patch(ws), patch.object(SchwabProvider, "_stream_tunables", _tunables(idle=5.0)):
                await p._run_streamer_loop()
            subs = [json.loads(x) for x in ws.sent[1:]]  # ws.sent[0] is LOGIN
            keys = {r["service"]: r["parameters"]["keys"] for batch in subs for r in batch["requests"]}
            assert keys == {"CHART_EQUITY": "AAPL,MSFT", "CHART_FUTURES": "/ES"}
