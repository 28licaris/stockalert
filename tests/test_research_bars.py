"""Bar-snapshot round trip (local parquet) for the MCPT dual-mode data source."""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
import pytest

from scripts.research_bars import load_bar_lists, load_frames, read_snapshot, write_snapshot

UTC = dt.timezone.utc


def _snapshot_df(n=40) -> pd.DataFrame:
    rows = []
    for sym, base in (("AAA", 50.0), ("BBB", 200.0)):
        for i in range(n):
            px = base + i
            rows.append({
                "symbol": sym,
                "timestamp": dt.datetime(2024, 1, 1, tzinfo=UTC) + dt.timedelta(days=i),
                "open": px, "high": px + 1.0, "low": px - 1.0, "close": px + 0.5,
                "volume": 1000.0 + i,
            })
    return pd.DataFrame(rows)


def test_roundtrip_and_shapes(tmp_path):
    dest = tmp_path / "snap.parquet"
    write_snapshot(_snapshot_df(), str(dest))

    frames = load_frames(str(dest))
    assert set(frames) == {"AAA", "BBB"}
    assert len(frames["AAA"]) == 40
    assert frames["AAA"].index.tz is not None  # tz-aware index

    bars = load_bar_lists(str(dest), symbols=["AAA"])
    assert set(bars) == {"AAA"}
    b = bars["AAA"][0]
    assert (b.symbol, b.open, b.volume) == ("AAA", 50.0, 1000.0)
    assert b.timestamp.tzinfo is not None


def test_window_and_symbol_filtering(tmp_path):
    dest = tmp_path / "snap.parquet"
    write_snapshot(_snapshot_df(), str(dest))
    frames = load_frames(str(dest), symbols=["BBB"],
                         start="2024-01-11", end="2024-01-20", min_bars=1)
    assert set(frames) == {"BBB"}
    assert len(frames["BBB"]) == 10
    assert frames["BBB"].index.min() == pd.Timestamp("2024-01-11", tz="UTC")


def test_schema_validation(tmp_path):
    bad = _snapshot_df().drop(columns=["volume"])
    dest = tmp_path / "bad.parquet"
    bad.to_parquet(dest, index=False)
    with pytest.raises(ValueError, match="missing columns"):
        read_snapshot(str(dest))


def test_min_bars_drop(tmp_path):
    df = _snapshot_df()
    df = pd.concat([df, df.iloc[:1].assign(symbol="TINY")])
    dest = tmp_path / "snap.parquet"
    write_snapshot(df, str(dest))
    frames = load_frames(str(dest), min_bars=30)
    assert "TINY" not in frames
    # numeric coercion survives
    assert isinstance(frames["AAA"]["close"].to_numpy()[0], np.float64)


def test_fomc_calendar_module_matches_csv():
    """The built-in Fed calendar (strategy default) must equal the collected CSV."""
    import pandas as pd

    from app.services.sim.strategies.fomc_calendar import FOMC_ANNOUNCEMENT_DATES

    csv = pd.read_csv("scripts/data/fomc_scheduled_meetings.csv",
                      comment="#")["announcement_date"].tolist()
    assert FOMC_ANNOUNCEMENT_DATES == csv
