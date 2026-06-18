"""Unit tests for futures universe resolution (F3)."""
from __future__ import annotations

from unittest.mock import MagicMock

from app.services.futures import universe
from app.services.futures.schemas import FUTURES_SEED_ROOTS


def test_resolve_seed_returns_seed_roots():
    assert universe.resolve_futures_spec("seed") == list(FUTURES_SEED_ROOTS)


def test_resolve_csv_enforces_leading_slash_and_upper():
    out = universe.resolve_futures_spec("es, /nq ,mcl")
    assert out == ["/ES", "/NQ", "/MCL"]


def test_resolve_active_reads_futures_universe(monkeypatch):
    client = MagicMock()
    client.query.return_value.result_rows = [("/ES",), ("/NQ",)]
    monkeypatch.setattr("app.db.client.get_client", lambda: client)
    assert universe.resolve_futures_spec("active") == ["/ES", "/NQ"]


def test_active_falls_back_to_seed_when_empty(monkeypatch):
    client = MagicMock()
    client.query.return_value.result_rows = []
    monkeypatch.setattr("app.db.client.get_client", lambda: client)
    assert universe.active_futures_roots() == list(FUTURES_SEED_ROOTS)


def test_active_falls_back_to_seed_on_read_error(monkeypatch):
    def _boom():
        raise RuntimeError("CH down")

    monkeypatch.setattr("app.db.client.get_client", _boom)
    assert universe.active_futures_roots() == list(FUTURES_SEED_ROOTS)
