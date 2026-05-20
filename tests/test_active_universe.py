"""
Tests for `app.services.universe.active_universe` post-FE-CONTRACTS-4-final.

Per the LOCKED architecture in
docs/standards/data/symbol_lifecycle.md:

  - The active universe is read from the canonical `stream_universe`
    CH table (via `stream_service.list_active_symbols()`), NOT from
    `SEED_SYMBOLS ∪ watchlists` anymore.
  - SEED_SYMBOLS is the cold-start fallback only.
  - `resolve_universe_spec` routes:
      "seed" / ""   → SEED_SYMBOLS
      "active"      → stream_universe
      "all" / "*"   → empty list (whole-market signal for Polygon flat-files)
      "AAPL,NVDA"   → explicit CSV
  - Nightly Schwab + silver build delegate through resolve_universe_spec.
  - Nightly Polygon defaults to "all" (whole market).
"""
from __future__ import annotations

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
# get_active_universe — reads from stream_universe
# ─────────────────────────────────────────────────────────────────────


class TestGetActiveUniverse:
    def test_reads_from_stream_universe(self) -> None:
        """Canonical case: stream_universe has rows → return them."""
        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            return_value={"AAPL", "NVDA", "PG"},
        ):
            symbols = get_active_universe()
        assert symbols == ["AAPL", "NVDA", "PG"]

    def test_empty_stream_universe_returns_empty(self) -> None:
        """Empty stream_universe = empty universe. No SEED_SYMBOLS
        fallback (it was removed when stream_universe became canonical
        — if no one has added a symbol, no symbol should be processed)."""
        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            return_value=set(),
        ):
            symbols = get_active_universe()
        assert symbols == []

    def test_ch_outage_returns_empty(self) -> None:
        """If stream_service.list_active_symbols raises, we degrade
        gracefully to empty (never propagate the exception). Nightlies
        no-op on empty universe — same as cold-start."""
        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            side_effect=RuntimeError("CH down"),
        ):
            symbols = get_active_universe()
        assert symbols == []

    def test_does_NOT_union_with_seed(self) -> None:
        """REGRESSION: pre-FE-CONTRACTS-4-final the function returned
        SEED ∪ <stream>. Post-final it returns ONLY stream_universe.
        Adding a symbol via stream completely replaces what nightlies see."""
        only_pg = {"PG"}
        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            return_value=only_pg,
        ):
            symbols = get_active_universe()
        assert symbols == ["PG"]
        # SEED constants are NOT silently included anymore.
        assert "AAPL" not in symbols  # AAPL is in SEED but not in stream

    def test_deprecated_include_seed_fallback_kwarg_is_ignored(self) -> None:
        """Back-compat: legacy callers may pass `include_seed_fallback=True`
        — it's accepted but does nothing now."""
        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            return_value=set(),
        ):
            # No crash; still returns empty since stream_universe is empty.
            assert get_active_universe(include_seed_fallback=True) == []

    def test_deprecated_watchlist_kinds_arg_is_ignored(self) -> None:
        """Back-compat: the `watchlist_kinds` keyword is preserved for
        legacy callers that haven't migrated, but it has no effect.
        Watchlists no longer drive the universe."""
        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            return_value={"AAPL"},
        ):
            # No crash, returns the stream-derived set regardless.
            symbols = get_active_universe(watchlist_kinds=["user", "baseline"])
        assert symbols == ["AAPL"]


# ─────────────────────────────────────────────────────────────────────
# resolve_universe_spec — config-string routing
# ─────────────────────────────────────────────────────────────────────


class TestResolveUniverseSpec:
    def test_seed_keyword_returns_legacy_seed(self) -> None:
        """`seed` spec is kept for legacy operator scripts that explicitly
        request the static SEED_SYMBOLS list. New code paths route
        through `active` (stream_universe)."""
        assert set(resolve_universe_spec("seed")) == set(SEED_SYMBOLS)
        assert set(resolve_universe_spec("SEED")) == set(SEED_SYMBOLS)

    def test_empty_or_none_defaults_to_active(self) -> None:
        """Empty spec defaults to active (stream_universe), NOT seed.
        Pre-FE-CONTRACTS-4-final empty defaulted to SEED_SYMBOLS; the
        new default is canonical stream_universe."""
        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            return_value={"PG"},
        ):
            assert resolve_universe_spec("") == ["PG"]
            assert resolve_universe_spec(None) == ["PG"]  # type: ignore[arg-type]

    def test_active_keyword_reads_stream_universe(self) -> None:
        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            return_value={"NVDA", "PG"},
        ):
            symbols = resolve_universe_spec("active")
        assert symbols == ["NVDA", "PG"]

    def test_active_aliases(self) -> None:
        """`active`, `universe`, `dynamic` all route to stream_universe."""
        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            return_value={"AAPL"},
        ):
            for alias in ("active", "Active", "ACTIVE", "universe", "dynamic"):
                assert resolve_universe_spec(alias) == ["AAPL"]

    def test_all_keyword_returns_empty_whole_market_signal(self) -> None:
        """`all` / `*` → empty list, the convention that Polygon flat-files
        interpret as 'no filter, import everything'."""
        assert resolve_universe_spec("all") == []
        assert resolve_universe_spec("*") == []
        assert resolve_universe_spec("ALL") == []

    def test_csv_list_uppercased(self) -> None:
        assert resolve_universe_spec("aapl,nvda,MSFT") == ["AAPL", "NVDA", "MSFT"]

    def test_csv_whitespace_tolerant(self) -> None:
        assert resolve_universe_spec(" AAPL ,  NVDA ,") == ["AAPL", "NVDA"]

    def test_canonical_constants(self) -> None:
        assert UNIVERSE_SPEC_SEED == "seed"
        assert UNIVERSE_SPEC_ACTIVE == "active"


# ─────────────────────────────────────────────────────────────────────
# Nightly delegations still work after the rewire
# ─────────────────────────────────────────────────────────────────────


class TestSchwabNightlyDelegation:
    def test_active_keyword_reads_stream_universe(self) -> None:
        from app.services.ingest.nightly_schwab_refresh import _resolve_symbols

        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            return_value={"PG"},
        ):
            assert _resolve_symbols("active") == ["PG"]

    def test_seed_keyword_still_returns_seed(self) -> None:
        from app.services.ingest.nightly_schwab_refresh import _resolve_symbols

        assert set(_resolve_symbols("seed")) == set(SEED_SYMBOLS)


class TestSilverNightlyDelegation:
    def test_active_keyword_reads_stream_universe(self) -> None:
        from app.services.silver.ohlcv.nightly import _resolve_symbols

        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            return_value={"PG"},
        ):
            assert _resolve_symbols("active") == ["PG"]


class TestPolygonNightlyDelegation:
    def test_all_keyword_returns_empty_for_whole_market(self) -> None:
        """Polygon flat-files: 'all' / '*' / '' → empty list = whole-market."""
        from app.services.ingest.nightly_polygon_refresh import (
            resolve_nightly_lake_symbols,
        )

        assert resolve_nightly_lake_symbols("all") == []
        assert resolve_nightly_lake_symbols("*") == []
        assert resolve_nightly_lake_symbols("") == []

    def test_active_keyword_reads_stream_universe(self) -> None:
        """Even though the production default is 'all', 'active' should
        still route through stream_universe for callers that override it."""
        from app.services.ingest.nightly_polygon_refresh import (
            resolve_nightly_lake_symbols,
        )

        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            return_value={"PG"},
        ):
            assert resolve_nightly_lake_symbols("active") == ["PG"]

    def test_seed_keyword_still_returns_seed(self) -> None:
        from app.services.ingest.nightly_polygon_refresh import (
            resolve_nightly_lake_symbols,
        )

        assert set(resolve_nightly_lake_symbols("seed")) == set(SEED_SYMBOLS)


# ─────────────────────────────────────────────────────────────────────
# SilverOhlcvBuild.run_nightly default
# ─────────────────────────────────────────────────────────────────────


class TestSilverBuildDefaultUniverse:
    def test_run_nightly_default_pulls_active_universe(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """`run_nightly()` with symbols=None resolves to stream_universe
        (via get_active_universe). Verified by spying on build_window."""
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
            "app.services.stream.stream_service.list_active_symbols",
            return_value={"NEW_DYNAMIC_SYM"},
        ):
            build.run_nightly(scan_corp_action_dirty=False)

        assert captured["symbols"] == ["NEW_DYNAMIC_SYM"]
