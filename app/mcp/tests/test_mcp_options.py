from __future__ import annotations

import asyncio
import json
from datetime import date
from typing import Any

import pytest

from app.mcp.server import mcp, register_all_tools
from app.services.options.schemas import (
    GammaExposureResponse,
    GammaExposureSnapshot,
    LatestGammaExposureResponse,
    LatestOptionContractsResponse,
    OptionContractsResponse,
    OptionContractSnapshot,
)


register_all_tools()


class _Reader:
    def __init__(self) -> None:
        self.calls = []

    def get_contracts(self, symbol, start, end, **kwargs):
        self.calls.append(("contracts", symbol, start, end, kwargs))
        return OptionContractsResponse(
            underlying_symbol=symbol,
            start=start,
            end=end,
            snapshot_id="123",
            count=1,
            contracts=[
                OptionContractSnapshot(
                    underlying_symbol=symbol,
                    option_symbol="AAPL  260717C00150000",
                    snapshot_ts=start,
                    put_call="CALL",
                    expiration_date=date(2026, 7, 17),
                    strike=150.0,
                    source="schwab-chain",
                )
            ],
        )

    def get_gamma_exposure(self, symbol, start, end, **kwargs):
        self.calls.append(("gex", symbol, start, end, kwargs))
        return GammaExposureResponse(
            underlying_symbol=symbol,
            start=start,
            end=end,
            aggregation_level=kwargs.get("aggregation_level"),
            snapshot_id="123",
            count=1,
            rows=[
                GammaExposureSnapshot(
                    underlying_symbol=symbol,
                    snapshot_ts=start,
                    underlying_price=150.0,
                    gamma_exposure=1000.0,
                    aggregation_level=kwargs.get("aggregation_level") or "total",
                    level_key="total",
                )
            ],
        )


class _HotReader:
    def __init__(self) -> None:
        self.calls = []

    def get_latest_contracts(self, symbol, **kwargs):
        self.calls.append(("latest_contracts", symbol, kwargs))
        return LatestOptionContractsResponse(
            underlying_symbol=symbol,
            count=1,
            contracts=[
                OptionContractSnapshot(
                    underlying_symbol=symbol,
                    option_symbol="AAPL  260717C00150000",
                    snapshot_ts="2026-06-27T14:00:00Z",
                    put_call="CALL",
                    expiration_date=date(2026, 7, 17),
                    strike=150.0,
                    source="schwab-chain",
                )
            ],
        )

    def get_latest_gamma_exposure(self, symbol, **kwargs):
        self.calls.append(("latest_gex", symbol, kwargs))
        return LatestGammaExposureResponse(
            underlying_symbol=symbol,
            aggregation_level=kwargs.get("aggregation_level"),
            count=1,
            rows=[
                GammaExposureSnapshot(
                    underlying_symbol=symbol,
                    snapshot_ts="2026-06-27T14:00:00Z",
                    underlying_price=150.0,
                    gamma_exposure=1000.0,
                    aggregation_level=kwargs.get("aggregation_level") or "total",
                    level_key="total",
                )
            ],
        )


@pytest.fixture
def stub_reader(monkeypatch):
    from app.mcp.tools import options as options_mod

    stub = _Reader()
    options_mod._reader.cache_clear()
    monkeypatch.setattr(options_mod, "_reader", lambda: stub)
    return stub


@pytest.fixture
def stub_hot_reader(monkeypatch):
    from app.mcp.tools import options as options_mod

    stub = _HotReader()
    options_mod._hot_reader.cache_clear()
    monkeypatch.setattr(options_mod, "_hot_reader", lambda: stub)
    return stub


def _unwrap(result: Any) -> dict:
    if isinstance(result, tuple) and len(result) == 2:
        return result[1]
    if isinstance(result, dict):
        return result
    if isinstance(result, (list, tuple)) and result:
        text = getattr(result[0], "text", None)
        if text:
            return json.loads(text)
    raise AssertionError(f"unexpected call_tool result shape: {type(result)} {result!r}")


def test_options_tools_registered() -> None:
    names = {tool.name for tool in asyncio.run(mcp.list_tools())}

    assert {
        "get_option_contracts",
        "get_option_gamma_exposure",
        "get_latest_option_contracts",
        "get_latest_option_gamma_exposure",
    } <= names


def test_call_get_option_contracts(stub_reader) -> None:
    result = asyncio.run(
        mcp.call_tool(
            "get_option_contracts",
            {
                "symbol": "AAPL",
                "start": "2026-06-27T14:00:00Z",
                "end": "2026-06-27T15:00:00Z",
                "expiration_date": "2026-07-17",
                "put_call": "CALL",
                "snapshot_id": "123",
                "limit": 10,
            },
        )
    )

    body = _unwrap(result)
    assert body["underlying_symbol"] == "AAPL"
    assert body["count"] == 1
    assert body["contracts"][0]["option_symbol"] == "AAPL  260717C00150000"
    call = stub_reader.calls[0]
    assert call[0] == "contracts"
    assert call[4]["expiration_date"] == date(2026, 7, 17)
    assert call[4]["put_call"] == "CALL"
    assert call[4]["snapshot_id"] == "123"
    assert call[4]["limit"] == 10


def test_call_get_option_gamma_exposure(stub_reader) -> None:
    result = asyncio.run(
        mcp.call_tool(
            "get_option_gamma_exposure",
            {
                "symbol": "AAPL",
                "start": "2026-06-27T14:00:00Z",
                "end": "2026-06-27T15:00:00Z",
                "aggregation_level": "total",
                "snapshot_id": "123",
                "limit": 5,
            },
        )
    )

    body = _unwrap(result)
    assert body["underlying_symbol"] == "AAPL"
    assert body["aggregation_level"] == "total"
    assert body["count"] == 1
    assert body["rows"][0]["level_key"] == "total"
    call = stub_reader.calls[0]
    assert call[0] == "gex"
    assert call[4]["aggregation_level"] == "total"
    assert call[4]["snapshot_id"] == "123"
    assert call[4]["limit"] == 5


def test_call_get_latest_option_contracts(stub_hot_reader) -> None:
    result = asyncio.run(
        mcp.call_tool(
            "get_latest_option_contracts",
            {
                "symbol": "AAPL",
                "expiration_date": "2026-07-17",
                "put_call": "CALL",
                "limit": 10,
            },
        )
    )

    body = _unwrap(result)
    assert body["source"] == "clickhouse"
    assert body["count"] == 1
    assert body["contracts"][0]["option_symbol"] == "AAPL  260717C00150000"
    call = stub_hot_reader.calls[0]
    assert call[0] == "latest_contracts"
    assert call[2]["expiration_date"] == date(2026, 7, 17)
    assert call[2]["put_call"] == "CALL"
    assert call[2]["limit"] == 10


def test_call_get_latest_option_gamma_exposure(stub_hot_reader) -> None:
    result = asyncio.run(
        mcp.call_tool(
            "get_latest_option_gamma_exposure",
            {"symbol": "AAPL", "aggregation_level": "total", "limit": 5},
        )
    )

    body = _unwrap(result)
    assert body["source"] == "clickhouse"
    assert body["aggregation_level"] == "total"
    assert body["rows"][0]["level_key"] == "total"
    call = stub_hot_reader.calls[0]
    assert call[0] == "latest_gex"
    assert call[2]["aggregation_level"] == "total"
    assert call[2]["limit"] == 5
