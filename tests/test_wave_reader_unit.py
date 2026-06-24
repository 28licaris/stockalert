"""EW-4: WaveReader mapping + HTTP route (no AWS)."""
from __future__ import annotations

import datetime as dt
import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import routes_wave
from app.indicators.pivots import PivotDetector
from app.services.readers.wave_reader import (
    WaveReader,
    WaveStateResponse,
    _from_labeling,
    _row_to_response,
)
from app.signals.elliott import WaveEngine
from tests._ewt_synthetic import AS_OF_WAVE3, synthetic_ohlc


def _labeling():
    close, high, low = synthetic_ohlc("up")
    piv = PivotDetector(period=3, source="hl").detect(close, high, low)
    return WaveEngine().label(piv, last_price=float(close.iloc[AS_OF_WAVE3]),
                              symbol="AAPL", interval="1d", as_of_index=AS_OF_WAVE3,
                              as_of=close.index[AS_OF_WAVE3].to_pydatetime())


def test_from_labeling_maps_primary_and_secondary():
    resp = _from_labeling(_labeling(), "compute")
    assert resp.symbol == "AAPL"
    assert resp.asset_class == "equity"
    assert resp.primary.structure == "impulse"
    assert resp.primary.current_wave == "3"
    assert resp.primary.pivots  # primary carries pivots
    assert resp.source == "compute"


def test_row_to_response_parses_stored_json():
    row = {
        "symbol": "NVDA", "interval": "1d", "asset_class": "equity",
        "as_of_date": dt.date(2026, 5, 27), "as_of_ts": None,
        "p_structure": "zigzag", "p_direction": "up", "p_current_wave": "complete",
        "p_degree": 1, "p_probability": 0.66, "p_confidence": 0.66,
        "p_invalidation": 163.5, "p_targets": json.dumps({"C=1.0xA": 200.0}),
        "p_pivots": "[]", "p_rationale": "done",
        "s_structure": None, "uncertainty": 0.337, "engine_ver": "ew2.0.0",
    }
    resp = _row_to_response(row)
    assert resp.primary.structure == "zigzag"
    assert resp.primary.targets == {"C=1.0xA": 200.0}
    assert resp.secondary is None
    assert resp.uncertainty == 0.337
    assert resp.source == "store"


class _FakeReader:
    def get_state(self, symbol, interval="1d", *, backend="auto"):
        return WaveStateResponse(symbol=symbol, interval=interval, asset_class="equity",
                                 uncertainty=0.2, engine_ver="ew2.0.0", source=backend)

    def get_history(self, symbol, interval="1d", *, start=None, end=None):
        return [self.get_state(symbol, interval)]


def _client():
    app = FastAPI()
    app.include_router(routes_wave.router, prefix="/api/v1")
    app.dependency_overrides[routes_wave.get_wave_reader] = lambda: _FakeReader()
    return TestClient(app)


def test_http_get_wave_state():
    r = _client().get("/api/v1/wave/AAPL?interval=1d&backend=compute")
    assert r.status_code == 200
    body = r.json()
    assert body["symbol"] == "AAPL"
    assert body["source"] == "compute"


def test_http_invalid_backend_rejected():
    assert _client().get("/api/v1/wave/AAPL?backend=bogus").status_code == 422


def test_http_history():
    r = _client().get("/api/v1/wave/AAPL/history")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_norm_symbol_preserves_futures_slash():
    from app.api.routes_wave import _norm_symbol
    assert _norm_symbol("AAPL") == "AAPL"
    assert _norm_symbol("/GC") == "/GC"      # futures root keeps its prefix
    assert _norm_symbol("//GC") == "/GC"     # collapse accidental duplicate
