"""
Unit tests for `app.data.seed_universe`.

Goals:
  * The seed list is exactly the documented size (100), every entry has a
    non-empty symbol/category/description, and the categories listed in
    ``CATEGORIES`` are an exact match for the categories actually used.
  * No accidental duplicates (a duplicate symbol would cause the lake archive
    to double-write partitions and the backfill scheduler to send 2x as many
    requests).
  * The shape of helper functions (``symbols_by_category``, ``category_counts``)
    matches consumer expectations.
  * The list is frozen / immutable so a runtime caller can't accidentally
    corrupt it via ``.append`` or ``.sort``.
"""
from __future__ import annotations

import pytest

from app.data.seed_universe import (
    CATEGORIES,
    SEED_COUNT,
    SEED_SYMBOLS,
    SEED_UNIVERSE,
    SeedTicker,
    category_counts,
    symbols_by_category,
)


class TestStructure:
    def test_seed_count_is_100(self):
        """Phase A spec is exactly 100 tickers. Bumping this requires updating
        docs/data_platform_plan.md and the scripts/README sample backfill command."""
        assert SEED_COUNT == 100
        assert len(SEED_UNIVERSE) == 100
        assert len(SEED_SYMBOLS) == 100

    def test_no_duplicate_symbols(self):
        """Duplicate symbols would corrupt the lake archive and double-bill
        the backfill schedule."""
        seen = set()
        dups = []
        for s in SEED_SYMBOLS:
            if s in seen:
                dups.append(s)
            seen.add(s)
        assert dups == [], f"duplicate symbols in SEED_UNIVERSE: {dups}"

    def test_all_symbols_are_uppercase_alnum(self):
        """Polygon Flat Files and ClickHouse both expect uppercase tickers.
        Anything else here would silently miss data."""
        for s in SEED_SYMBOLS:
            assert s == s.upper(), f"non-uppercase symbol: {s!r}"
            assert s.replace(".", "").isalnum(), f"non-alnum symbol: {s!r}"

    def test_all_entries_have_required_fields(self):
        for t in SEED_UNIVERSE:
            assert isinstance(t, SeedTicker)
            assert t.symbol, f"empty symbol in entry {t!r}"
            assert t.category, f"empty category in entry {t!r}"
            assert t.description, f"empty description in entry {t!r}"


class TestCategories:
    def test_categories_constant_matches_used_categories(self):
        """``CATEGORIES`` is the source of truth for valid buckets. Drift
        between the constant and the actual entries is a code smell."""
        used = {t.category for t in SEED_UNIVERSE}
        declared = set(CATEGORIES)
        assert used == declared, (
            f"category drift — used but not declared: {used - declared}; "
            f"declared but not used: {declared - used}"
        )

    def test_documented_bucket_sizes(self):
        """Counts per bucket match the README composition table so changes to
        either stay in sync. Adjusting these requires updating the docs."""
        counts = category_counts()
        assert counts == {
            "mega_cap_tech":        15,
            "semiconductors":       10,
            "broad_etf":            10,
            "sector_etf":           10,
            "commodity_metals":     10,
            "bond_volatility_etf":   5,
            "financials":            8,
            "healthcare":            8,
            "consumer":              8,
            "industrials_energy":    8,
            "momentum":              8,
        }

    def test_commodity_metals_contains_user_requested_etfs(self):
        """Regression: this PR added the commodity/metals bucket per user
        request. Ensure GLD/SLV (gold/silver) and at least one industrial
        commodity (USO oil) survive a refactor."""
        metals = symbols_by_category("commodity_metals")
        assert {"GLD", "SLV", "IAU", "USO", "DBC", "COPX"}.issubset(set(metals))


class TestHelpers:
    def test_symbols_by_category_filters_correctly(self):
        techs = symbols_by_category("mega_cap_tech")
        assert "AAPL" in techs
        assert "MSFT" in techs
        assert "NVDA" in techs
        # Cross-category leakage check.
        assert "GLD" not in techs
        assert "JPM" not in techs

    def test_symbols_by_category_unknown_returns_empty(self):
        """Unknown category must not raise — protects routes that take user
        input."""
        assert symbols_by_category("not_a_real_bucket") == []
        assert symbols_by_category("") == []

    def test_category_counts_returns_all_buckets(self):
        counts = category_counts()
        # Every documented bucket appears in the result, even those with 0
        # entries (none currently, but the dict must be exhaustive).
        for c in CATEGORIES:
            assert c in counts

    def test_category_counts_sum_equals_seed_count(self):
        assert sum(category_counts().values()) == SEED_COUNT


class TestImmutability:
    def test_seed_universe_is_a_tuple(self):
        """Tuples are immutable; lists are not. Switching this would let any
        runtime caller mutate the universe and silently corrupt downstream
        archives."""
        assert isinstance(SEED_UNIVERSE, tuple)
        assert isinstance(SEED_SYMBOLS, tuple)
        assert isinstance(CATEGORIES, tuple)

    def test_seed_ticker_is_frozen(self):
        """``@dataclass(frozen=True)`` guards against in-place mutation of a
        single row's category/description."""
        t = SEED_UNIVERSE[0]
        with pytest.raises((AttributeError, Exception)):
            t.symbol = "ZZZZ"  # type: ignore[misc]
