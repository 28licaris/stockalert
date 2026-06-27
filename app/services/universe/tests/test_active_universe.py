"""
Tests for `app.services.universe.active_universe` post-FE-CONTRACTS-4-final.

Per the LOCKED architecture in
docs/standards/data/symbol_lifecycle.md:

  - The active universe is read from the canonical `stream_universe`
    CH table (via `stream_service.list_active_symbols()`), NOT from
    `SEED_SYMBOLS ∪ watchlists` anymore.
  - `resolve_universe_spec` routes:
      "" / "active" → stream_universe
      "seed"         → rejected (retired)
      "all" / "*"   → empty list (whole-market signal for Polygon flat-files)
      "AAPL,NVDA"   → explicit CSV
  - Nightly Schwab + silver build delegate through resolve_universe_spec.
  - Nightly Polygon defaults to "all" (whole market).
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from app.services.universe import (
    UNIVERSE_SPEC_ACTIVE,
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

    def test_ch_outage_propagates(self) -> None:
        """An unavailable source of truth must fail loudly."""
        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            side_effect=RuntimeError("CH down"),
        ):
            with pytest.raises(RuntimeError, match="CH down"):
                get_active_universe()

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


# ─────────────────────────────────────────────────────────────────────
# resolve_universe_spec — config-string routing
# ─────────────────────────────────────────────────────────────────────


class TestResolveUniverseSpec:
    def test_seed_keyword_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="static seed universe is retired"):
            resolve_universe_spec("seed")

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

    def test_seed_keyword_is_rejected(self) -> None:
        from app.services.ingest.nightly_schwab_refresh import _resolve_symbols

        with pytest.raises(ValueError, match="static seed universe is retired"):
            _resolve_symbols("seed")


class TestPolygonNightlyDelegation:
    def test_all_keyword_returns_empty_for_whole_market(self) -> None:
        """Polygon flat-files: 'all' / '*' / '' → empty list = whole-market."""
        from app.services.ingest.nightly_equities_polygon_refresh import (
            resolve_nightly_lake_symbols,
        )

        assert resolve_nightly_lake_symbols("all") == []
        assert resolve_nightly_lake_symbols("*") == []
        assert resolve_nightly_lake_symbols("") == []

    def test_active_keyword_reads_stream_universe(self) -> None:
        """Even though the production default is 'all', 'active' should
        still route through stream_universe for callers that override it."""
        from app.services.ingest.nightly_equities_polygon_refresh import (
            resolve_nightly_lake_symbols,
        )

        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            return_value={"PG"},
        ):
            assert resolve_nightly_lake_symbols("active") == ["PG"]

    def test_seed_keyword_is_rejected(self) -> None:
        from app.services.ingest.nightly_equities_polygon_refresh import (
            resolve_nightly_lake_symbols,
        )

        with pytest.raises(ValueError, match="static seed universe is retired"):
            resolve_nightly_lake_symbols("seed")


# ─────────────────────────────────────────────────────────────────────
# SilverOhlcvBuild.run_nightly default
# ─────────────────────────────────────────────────────────────────────


# CV14: TestSilverBuildDefaultUniverse removed — exercised the deleted
# SilverOhlcvBuild class. Active-universe resolution is now covered by
# TestResolveUniverseSpec above + the live nightly_equities_polygon_refresh /
# nightly_schwab_refresh test paths.
