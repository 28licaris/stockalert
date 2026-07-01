"""Strategy library: persistence, S3-backup result, and the public/private wall."""
from __future__ import annotations

import datetime as dt

import pytest

from app.services.sim.library import service as lib
from app.services.sim.library.schemas import StrategyDefinition, StrategyPublic
from app.services.sim.paper.schemas import PaperRunConfig, PaperState
from app.services.sim.paper.service import save_state

UTC = dt.timezone.utc


@pytest.fixture(autouse=True)
def _no_clickhouse(monkeypatch):
    """Keep these unit tests off the live CH tier — exercise only the local
    file cache for both the library and the linked paper state."""
    import app.services.sim.paper.service as paper_svc

    monkeypatch.setattr(lib, "_ch_save_definition", lambda d, now: None)
    monkeypatch.setattr(lib, "_ch_load_definition", lambda name: None)
    monkeypatch.setattr(lib, "_ch_list_definitions", lambda: [])
    monkeypatch.setattr(paper_svc, "_ch_save_state", lambda state: None)
    monkeypatch.setattr(paper_svc, "_ch_load_state", lambda name: None)


def _defn():
    return StrategyDefinition(
        name="t_strat", title="Test", tagline="hook", description="desc",
        category="momentum", visibility="subscribers",
        config={"source": "breakout", "secret_param": 42},  # the recipe
    )


def test_register_saves_local_and_reports_backup(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKALERT_STRATEGY_DIR", str(tmp_path))
    monkeypatch.setattr(lib, "_backup_to_s3", lambda d, stamp: ("s3://bucket/x.json", None))
    res = lib.register(_defn())
    assert res.s3_uri == "s3://bucket/x.json" and res.s3_error is None
    assert (tmp_path / "t_strat.json").exists()
    loaded = lib.load_definition("t_strat")
    assert loaded is not None and loaded.config["secret_param"] == 42


def test_public_view_has_no_config():
    # The security guarantee: StrategyPublic structurally cannot carry the recipe.
    assert "config" not in StrategyPublic.model_fields
    pub = lib.to_public(_defn())
    assert "config" not in pub.model_dump()
    assert pub.title == "Test" and pub.tagline == "hook"


def test_alerts_expose_signals_not_recipe(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKALERT_PAPER_DIR", str(tmp_path))
    go = dt.datetime(2026, 1, 1, tzinfo=UTC)
    cfg = PaperRunConfig(name="t_strat", strategy="alert_driven", strategy_params={},
                         symbols=["AAPL"], starting_cash=100_000.0,
                         history_start=go, go_live=go)
    state = PaperState(
        config=cfg, computed_through=dt.datetime(2026, 3, 1, tzinfo=UTC),
        equity_curve=[(go, 100_000.0), (dt.datetime(2026, 3, 1, tzinfo=UTC), 110_000.0)],
        trades=[{"symbol": "MU", "side": "sell", "quantity": 10, "price": 120.0,
                 "timestamp": dt.datetime(2026, 2, 1, tzinfo=UTC), "realized_pnl": 200.0,
                 "holding_days": 5, "is_closing": True}],
        open_positions=[{"symbol": "NVDA", "quantity": 10, "avg_entry_price": 100.0,
                         "current_price": 115.0, "stop_price": 92.0, "target_price": 130.0,
                         "entry_time": dt.datetime(2026, 2, 20, tzinfo=UTC), "unrealized_pnl": 150.0}],
    )
    save_state(state)
    alerts = lib.get_alerts("t_strat")
    opens = [a for a in alerts if a.status == "open"]
    closed = [a for a in alerts if a.status == "closed"]
    assert opens and opens[0].symbol == "NVDA"
    assert opens[0].entry == 100.0 and opens[0].stop == 92.0 and opens[0].target == 130.0
    assert closed and closed[0].symbol == "MU" and closed[0].pnl == 200.0
    # alerts never carry config
    assert all("config" not in a.model_dump() for a in alerts)
