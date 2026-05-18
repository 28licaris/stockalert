"""
Tests for `app.services.universe.active_universe` — the G1 dynamic
universe resolver.

Verifies:
  - SEED_SYMBOLS is the floor (always included when include_seed=True)
  - Watchlist symbols are unioned in
  - Output is sorted + deduplicated
  - CH outage → graceful fallback to seed-only
  - resolve_universe_spec routes "seed" / "active" / CSV correctly
  - Each nightly's symbol resolver delegates through resolve_universe_spec
    (so adding "active" to env config works system-wide)
"""
from __future__ import annotations

from typing import Iterable, Optional
from unittest.mock import patch

import pytest

from app.data.seed_universe import SEED_SYMBOLS
from app.services.universe import (
    UNIVERSE_SPEC_ACTIVE,
    UNIVERSE_SPEC_SEED,
    get_active_universe,
    resolve_universe_spec,
)


# ─────────────────────────────────────────────────────────────────────
# get_active_universe — happy path + degradation
# ─────────────────────────────────────────────────────────────────────


class TestGetActiveUniverse:
    def test_includes_seed_when_no_watchlist(self) -> None:
        # Patch the late-bound watchlist_repo import inside the function.
        with patch(
            "app.db.watchlist_repo.list_all_active_symbols",
            return_value=set(),
        ):
            symbols = get_active_universe()
        assert set(symbols) == set(SEED_SYMBOLS)
        # Sorted output.
        assert symbols == sorted(symbols)

    def test_unions_seed_with_watchlists(self) -> None:
        wl_only = {"BRK.B", "PYPL", "NVDA"}  # NVDA also in seed → dedup
        with patch(
            "app.db.watchlist_repo.list_all_active_symbols",
            return_value=wl_only,
        ):
            symbols = get_active_universe()
        # All seed members present + all watchlist members present.
        assert set(SEED_SYMBOLS).issubset(set(symbols))
        assert "BRK.B" in symbols
        assert "PYPL" in symbols
        # No duplicates (NVDA appears once).
        assert symbols.count("NVDA") == 1

    def test_exclude_seed_returns_watchlist_only(self) -> None:
        wl_only = {"BRK.B", "PYPL"}
        with patch(
            "app.db.watchlist_repo.list_all_active_symbols",
            return_value=wl_only,
        ):
            symbols = get_active_universe(include_seed=False)
        assert set(symbols) == wl_only

    def test_kinds_filter_passes_through(self) -> None:
        captured: dict = {}

        def _spy(kinds: Optional[Iterable[str]] = None) -> set[str]:
            captured["kinds"] = kinds
            return {"AAPL"}

        with patch("app.db.watchlist_repo.list_all_active_symbols", _spy):
            get_active_universe(watchlist_kinds=["user", "baseline"])
        assert captured["kinds"] == ["user", "baseline"]

    def test_ch_outage_falls_back_to_seed_only(self) -> None:
        # If watchlist_repo raises (CH down), we degrade — never raise.
        with patch(
            "app.db.watchlist_repo.list_all_active_symbols",
            side_effect=RuntimeError("CH connection refused"),
        ):
            symbols = get_active_universe()
        assert set(symbols) == set(SEED_SYMBOLS)


# ─────────────────────────────────────────────────────────────────────
# resolve_universe_spec — config-spec routing
# ─────────────────────────────────────────────────────────────────────


class TestResolveUniverseSpec:
    def test_seed_keyword_returns_seed(self) -> None:
        assert set(resolve_universe_spec("seed")) == set(SEED_SYMBOLS)
        assert set(resolve_universe_spec("SEED")) == set(SEED_SYMBOLS)
        assert set(resolve_universe_spec("")) == set(SEED_SYMBOLS)
        assert set(resolve_universe_spec(None)) == set(SEED_SYMBOLS)  # type: ignore[arg-type]

    def test_active_keyword_calls_get_active_universe(self) -> None:
        with patch(
            "app.db.watchlist_repo.list_all_active_symbols",
            return_value={"NVDA", "PYPL"},
        ):
            symbols = resolve_universe_spec("active")
        assert "PYPL" in symbols
        assert set(SEED_SYMBOLS).issubset(set(symbols))

    def test_active_aliases(self) -> None:
        with patch(
            "app.db.watchlist_repo.list_all_active_symbols",
            return_value=set(),
        ):
            for alias in ("active", "Active", "ACTIVE", "universe", "dynamic"):
                assert set(resolve_universe_spec(alias)) == set(SEED_SYMBOLS)

    def test_csv_list_uppercased(self) -> None:
        assert resolve_universe_spec("aapl,nvda,MSFT") == ["AAPL", "NVDA", "MSFT"]

    def test_csv_whitespace_tolerant(self) -> None:
        assert resolve_universe_spec(" AAPL ,  NVDA ,") == ["AAPL", "NVDA"]

    def test_canonical_constants(self) -> None:
        # Public constants match the literals callers should expect.
        assert UNIVERSE_SPEC_SEED == "seed"
        assert UNIVERSE_SPEC_ACTIVE == "active"


# ─────────────────────────────────────────────────────────────────────
# Each nightly's _resolve_symbols delegates to resolve_universe_spec
# (so "active" works system-wide, not just in the universe module).
# ─────────────────────────────────────────────────────────────────────


class TestSchwabNightlyDelegation:
    def test_active_keyword_routes_through_universe(self) -> None:
        from app.services.ingest.nightly_schwab_refresh import _resolve_symbols

        with patch(
            "app.db.watchlist_repo.list_all_active_symbols",
            return_value={"NEW_SYMBOL"},
        ):
            symbols = _resolve_symbols("active")
        assert "NEW_SYMBOL" in symbols
        assert set(SEED_SYMBOLS).issubset(set(symbols))

    def test_seed_keyword_still_returns_seed(self) -> None:
        from app.services.ingest.nightly_schwab_refresh import _resolve_symbols

        assert set(_resolve_symbols("seed")) == set(SEED_SYMBOLS)


class TestSilverNightlyDelegation:
    def test_active_keyword_routes_through_universe(self) -> None:
        from app.services.silver.ohlcv.nightly import _resolve_symbols

        with patch(
            "app.db.watchlist_repo.list_all_active_symbols",
            return_value={"NEW_SYMBOL"},
        ):
            symbols = _resolve_symbols("active")
        assert "NEW_SYMBOL" in symbols


class TestPolygonNightlyDelegation:
    def test_active_keyword_unions_seed_and_watchlists(self) -> None:
        from app.services.ingest.nightly_polygon_refresh import (
            resolve_nightly_lake_symbols,
        )

        with patch(
            "app.db.watchlist_repo.list_all_active_symbols",
            return_value={"NEW_SYMBOL"},
        ):
            symbols = resolve_nightly_lake_symbols("active")
        assert "NEW_SYMBOL" in symbols
        assert set(SEED_SYMBOLS).issubset(set(symbols))

    def test_all_keyword_still_returns_empty_for_whole_market(self) -> None:
        """Polygon flat-files special case: 'all'/'*'/'' → empty list,
        which the FlatFilesBackfillService interprets as 'import every
        symbol in the flat file' (free since flat-files are whole-market
        anyway)."""
        from app.services.ingest.nightly_polygon_refresh import (
            resolve_nightly_lake_symbols,
        )

        assert resolve_nightly_lake_symbols("all") == []
        assert resolve_nightly_lake_symbols("*") == []
        assert resolve_nightly_lake_symbols("") == []

    def test_seed_keyword_still_returns_seed(self) -> None:
        from app.services.ingest.nightly_polygon_refresh import (
            resolve_nightly_lake_symbols,
        )

        assert set(resolve_nightly_lake_symbols("seed")) == set(SEED_SYMBOLS)


# ─────────────────────────────────────────────────────────────────────
# SilverOhlcvBuild.run_nightly default now uses get_active_universe
# ─────────────────────────────────────────────────────────────────────


class TestSilverBuildDefaultUniverse:
    def test_run_nightly_default_pulls_active_universe(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When `run_nightly()` is called with symbols=None, it should
        resolve to get_active_universe() = SEED ∪ watchlist symbols.
        Verify by spying on what gets passed to build_window."""
        from app.services.silver.ohlcv.build import SilverOhlcvBuild

        build = SilverOhlcvBuild(
            catalog=object(), ohlcv_table=object(),
            bar_quality_table=object(),
            provider_precedence=["polygon", "schwab"],
        )
        captured: dict = {}

        def _spy(symbols, start, end):
            captured["symbols"] = list(symbols)
            from app.services.silver.ohlcv.build import BuildResult
            from datetime import datetime, timezone
            t = datetime.now(timezone.utc)
            return BuildResult(run_id="x", started_at=t, finished_at=t)

        monkeypatch.setattr(build, "build_window", _spy)
        with patch(
            "app.db.watchlist_repo.list_all_active_symbols",
            return_value={"NEW_DYNAMIC_SYM"},
        ):
            # scan_corp_action_dirty=False so the test stays focused on
            # the universe-default path and doesn't touch the TA-5.1.9
            # corp_actions scan (which needs a real catalog).
            build.run_nightly(scan_corp_action_dirty=False)

        assert "NEW_DYNAMIC_SYM" in captured["symbols"]
        assert set(SEED_SYMBOLS).issubset(set(captured["symbols"]))
