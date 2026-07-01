"""Paper-trading forward-slice + state persistence (M3)."""
from __future__ import annotations

import datetime as dt

import pytest

from app.services.sim.paper.schemas import PaperRunConfig, PaperState
from app.services.sim.paper.service import build_status, load_state, save_state

UTC = dt.timezone.utc


@pytest.fixture(autouse=True)
def _no_clickhouse(monkeypatch):
    """Keep these unit tests off the live CH tier — exercise only the local
    file cache (CH persistence is covered by integration tests)."""
    import app.services.sim.paper.service as svc

    monkeypatch.setattr(svc, "_ch_save_state", lambda state: None)
    monkeypatch.setattr(svc, "_ch_load_state", lambda name: None)


def _cfg():
    return PaperRunConfig(
        name="t_run", strategy="alert_driven", strategy_params={"source": "breakout"},
        symbols=["AAPL"], starting_cash=100_000.0,
        history_start=dt.datetime(2026, 1, 1, tzinfo=UTC),
        go_live=dt.datetime(2026, 6, 1, tzinfo=UTC),
    )


def _state():
    go = dt.datetime(2026, 6, 1, tzinfo=UTC)
    curve = [
        (dt.datetime(2026, 5, 1, tzinfo=UTC), 100_000.0),
        (dt.datetime(2026, 5, 20, tzinfo=UTC), 105_000.0),
        (go, 110_000.0),                                   # baseline = equity at go-live
        (dt.datetime(2026, 6, 15, tzinfo=UTC), 120_000.0),
        (dt.datetime(2026, 6, 30, tzinfo=UTC), 121_000.0),
    ]
    trades = [
        {"symbol": "X", "side": "sell", "quantity": 1, "price": 1, "timestamp": dt.datetime(2026, 5, 20, tzinfo=UTC), "realized_pnl": 5_000.0, "holding_days": 3, "is_closing": True},
        {"symbol": "Y", "side": "buy", "quantity": 1, "price": 1, "timestamp": dt.datetime(2026, 6, 10, tzinfo=UTC), "realized_pnl": 0.0, "holding_days": 0, "is_closing": False},
        {"symbol": "Y", "side": "sell", "quantity": 1, "price": 1, "timestamp": dt.datetime(2026, 6, 15, tzinfo=UTC), "realized_pnl": 10_000.0, "holding_days": 5, "is_closing": True},
        {"symbol": "Z", "side": "sell", "quantity": 1, "price": 1, "timestamp": dt.datetime(2026, 6, 30, tzinfo=UTC), "realized_pnl": -1_000.0, "holding_days": 2, "is_closing": True},
    ]
    return PaperState(config=_cfg(), last_run_at=dt.datetime(2026, 6, 30, tzinfo=UTC),
                      computed_through=dt.datetime(2026, 6, 30, tzinfo=UTC),
                      equity_curve=curve, trades=trades,
                      open_positions=[{"symbol": "NVDA", "quantity": 10, "avg_entry_price": 100.0,
                                       "entry_time": dt.datetime(2026, 6, 20, tzinfo=UTC), "unrealized_pnl": 250.0}])


def test_forward_slice_rebased_to_starting_capital():
    s = build_status(_state())                       # default capital = cfg 100k, start = go_live
    assert s.starting_capital == 100_000.0           # forward record starts at configured capital
    assert abs(s.current_balance - 110_000.0) < 1e-6  # 121k * (100k/110k baseline at go-live)
    assert abs(s.forward_return - (121_000 / 110_000 - 1)) < 1e-9   # return invariant to rebasing


def test_rebase_and_start_override():
    # Replay from an earlier date with a custom capital; return spans the wider window.
    s = build_status(_state(), start=dt.datetime(2026, 5, 1, tzinfo=UTC), capital=50_000.0)
    assert s.starting_capital == 50_000.0
    assert s.start_date.date() == dt.date(2026, 5, 1)
    assert abs(s.forward_return - (121_000 / 100_000 - 1)) < 1e-9   # 5/1 baseline=100k → +21%


def test_forward_counts_only_post_golive_closed_trades():
    s = build_status(_state())
    assert s.forward_n_trades == 2                    # the +10k win and -1k loss (pre-go-live 5k excluded)
    assert abs(s.forward_win_rate - 0.5) < 1e-9
    assert s.days_live == 29
    assert s.n_open_positions == 1 and s.open_positions[0].symbol == "NVDA"


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("STOCKALERT_PAPER_DIR", str(tmp_path))
    st = _state()
    save_state(st)
    loaded = load_state("t_run")
    assert loaded is not None
    assert loaded.config.name == "t_run"
    assert len(loaded.equity_curve) == 5
    assert build_status(loaded).forward_n_trades == 2


def _state_with_today_entry():
    st = _state()
    st.open_positions.append({
        "symbol": "AVGO", "quantity": 5, "avg_entry_price": 200.0,
        "entry_time": dt.datetime(2026, 6, 30, tzinfo=UTC), "unrealized_pnl": 100.0,
    })
    return st


def test_todays_activity_flags_entries_and_exits():
    from app.services.sim.paper.service import build_status as bs
    s = bs(_state_with_today_entry())
    assert {p.symbol for p in s.today_entries} == {"AVGO"}   # opened on computed_through (6-30)
    assert {t.symbol for t in s.today_exits} == {"Z"}        # closed on 6-30 (NVDA held since 6-20 → not today)


def test_append_alerts_idempotent(tmp_path, monkeypatch):
    from app.services.sim.paper.service import append_alerts, build_status as bs
    monkeypatch.setenv("STOCKALERT_PAPER_DIR", str(tmp_path))
    s = bs(_state_with_today_entry())
    assert append_alerts(s) >= 1                              # writes AVGO entry + Z exit
    assert append_alerts(s) == 0                              # same date → no duplicate
    assert (tmp_path / "t_run_alerts.jsonl").exists()


def test_export_csv_has_summary_trades_and_positions():
    from app.services.sim.paper.service import export_csv
    csv_text = export_csv(_state_with_today_entry())
    assert "# Paper trading log" in csv_text
    assert "# Starting balance" in csv_text and "# Ending balance" in csv_text
    assert "CLOSED TRADES" in csv_text and "OPEN POSITIONS" in csv_text
    assert "symbol,side,entry_date,exit_date,held_days,quantity,entry_price,exit_price,realized_pnl" in csv_text
    assert "NVDA" in csv_text   # the open position
