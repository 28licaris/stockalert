from __future__ import annotations

from datetime import date

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_options
from app.api.routes_options import get_options_reader
from app.services.options.schemas import (
    GammaExposureResponse,
    GammaExposureSnapshot,
    OptionContractsResponse,
    OptionContractSnapshot,
)


def _make_app() -> FastAPI:
    app = FastAPI()
    app.include_router(routes_options.router, prefix="/api", tags=["Options"])
    return app


class _Reader:
    def __init__(self, *, raises: Exception | None = None) -> None:
        self.raises = raises
        self.calls = []

    def get_contracts(self, symbol, start, end, **kwargs):
        self.calls.append(("contracts", symbol, start, end, kwargs))
        if self.raises:
            raise self.raises
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
        if self.raises:
            raise self.raises
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


def test_options_contracts_route_delegates_to_reader() -> None:
    app = _make_app()
    reader = _Reader()
    app.dependency_overrides[get_options_reader] = lambda: reader

    with TestClient(app) as client:
        resp = client.get(
            "/api/options/contracts",
            params={
                "symbol": "AAPL",
                "start": "2026-06-27T14:00:00Z",
                "end": "2026-06-27T15:00:00Z",
                "expiration_date": "2026-07-17",
                "put_call": "CALL",
                "snapshot_id": "123",
                "limit": 10,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["underlying_symbol"] == "AAPL"
    assert body["count"] == 1
    assert body["contracts"][0]["option_symbol"] == "AAPL  260717C00150000"
    call = reader.calls[0]
    assert call[0] == "contracts"
    assert call[4]["expiration_date"] == date(2026, 7, 17)
    assert call[4]["put_call"] == "CALL"
    assert call[4]["snapshot_id"] == "123"
    assert call[4]["limit"] == 10


def test_options_gex_route_delegates_to_reader() -> None:
    app = _make_app()
    reader = _Reader()
    app.dependency_overrides[get_options_reader] = lambda: reader

    with TestClient(app) as client:
        resp = client.get(
            "/api/options/gex",
            params={
                "symbol": "AAPL",
                "start": "2026-06-27T14:00:00Z",
                "end": "2026-06-27T15:00:00Z",
                "aggregation_level": "total",
                "limit": 5,
            },
        )

    assert resp.status_code == 200
    body = resp.json()
    assert body["underlying_symbol"] == "AAPL"
    assert body["aggregation_level"] == "total"
    assert body["count"] == 1
    assert body["rows"][0]["level_key"] == "total"
    assert reader.calls[0][4]["aggregation_level"] == "total"
    assert reader.calls[0][4]["limit"] == 5


def test_options_route_value_error_returns_400() -> None:
    app = _make_app()
    app.dependency_overrides[get_options_reader] = lambda: _Reader(
        raises=ValueError("bad snapshot id")
    )

    with TestClient(app) as client:
        resp = client.get(
            "/api/options/gex",
            params={
                "symbol": "AAPL",
                "start": "2026-06-27T14:00:00Z",
                "end": "2026-06-27T15:00:00Z",
            },
        )

    assert resp.status_code == 400
    assert "bad snapshot id" in resp.json()["detail"]


def test_options_route_unexpected_error_returns_500() -> None:
    app = _make_app()
    app.dependency_overrides[get_options_reader] = lambda: _Reader(
        raises=RuntimeError("glue down")
    )

    with TestClient(app) as client:
        resp = client.get(
            "/api/options/contracts",
            params={
                "symbol": "AAPL",
                "start": "2026-06-27T14:00:00Z",
                "end": "2026-06-27T15:00:00Z",
            },
        )

    assert resp.status_code == 500
    assert "options contract read failed" in resp.json()["detail"]
