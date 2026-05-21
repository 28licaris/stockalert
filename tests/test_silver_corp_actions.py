"""
Unit tests for corp-actions ingest + silver build.

Tests are pure (no network, no S3) — they use stubbed Polygon
clients + injected PyIceberg tables. Live verification happens in
the operator runbook (TA-5.0 step 9), not in pytest.

What's pinned here:
- Polygon REST row → CorpAction mapping (splits + dividends + edge cases).
- Polygon ingest's Arrow serialization preserves the bronze schema exactly.
- Silver build's merge-with-precedence picks the higher-priority provider on overlap,
  and falls through to the next provider when the higher one has no row.
- Re-stamping ingestion metadata on the silver-build side.
- Empty / missing-table edge cases (silver_build returns empty Arrow, not crash).
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from typing import AsyncIterator

import pyarrow as pa
import pytest

from app.providers.polygon_corp_actions import (
    PolygonCorpActionsClient,
    _DIVIDEND_TYPE_MAP,
)
from app.services.silver.corp_actions.build import (
    SilverCorpActionsBuild,
    _SILVER_CORP_ACTIONS_ARROW,
)
from app.services.ingest.corp_actions import (
    PolygonCorpActionsIngest,
    _CORP_ACTIONS_ARROW,
)
from app.services.silver.schemas import CorpAction


# ─────────────────────────────────────────────────────────────────────
# Polygon REST → CorpAction mapping
# ─────────────────────────────────────────────────────────────────────


class TestSplitMapping:
    """`_split_to_corp_action` correctly normalizes Polygon's /splits rows."""

    def test_basic_4_for_1(self) -> None:
        row = {
            "execution_date": "2020-08-31",
            "ticker": "AAPL",
            "split_from": 1,
            "split_to": 4,
        }
        action = PolygonCorpActionsClient._split_to_corp_action(row)
        assert action.symbol == "AAPL"
        assert action.action_type == "split"
        assert action.factor == 4.0
        assert action.cash_amount is None
        assert action.source_provider == "polygon"
        assert action.ex_date == date(2020, 8, 31)

    def test_reverse_split(self) -> None:
        """1-for-2 reverse split → factor 0.5."""
        row = {
            "execution_date": "2023-01-15",
            "ticker": "XYZ",
            "split_from": 2,
            "split_to": 1,
        }
        action = PolygonCorpActionsClient._split_to_corp_action(row)
        assert action.factor == 0.5

    def test_lowercase_ticker_gets_uppercased(self) -> None:
        row = {
            "execution_date": "2024-06-10",
            "ticker": "nvda",
            "split_from": 1,
            "split_to": 10,
        }
        action = PolygonCorpActionsClient._split_to_corp_action(row)
        assert action.symbol == "NVDA"

    def test_split_from_zero_treated_as_one(self) -> None:
        """Defensive: Polygon should never send split_from=0 but if it
        does, we don't crash (treat as 1)."""
        row = {
            "execution_date": "2024-06-10",
            "ticker": "NVDA",
            "split_from": 0,
            "split_to": 10,
        }
        action = PolygonCorpActionsClient._split_to_corp_action(row)
        assert action.factor == 10.0


class TestDividendMapping:
    """`_dividend_to_corp_action` handles the dividend_type variants."""

    def test_cash_dividend(self) -> None:
        row = {
            "ex_dividend_date": "2020-08-07",
            "ticker": "AAPL",
            "cash_amount": 0.82,
            "declaration_date": "2020-07-30",
            "dividend_type": "CD",
        }
        action = PolygonCorpActionsClient._dividend_to_corp_action(row)
        assert action is not None
        assert action.action_type == "cash_dividend"
        assert action.cash_amount == 0.82
        assert action.factor is None
        assert action.announced_at == datetime(2020, 7, 30, tzinfo=timezone.utc)

    def test_stock_dividend(self) -> None:
        row = {
            "ex_dividend_date": "2024-04-10",
            "ticker": "ABC",
            "cash_amount": None,
            "dividend_type": "SC",
        }
        action = PolygonCorpActionsClient._dividend_to_corp_action(row)
        assert action is not None
        assert action.action_type == "stock_dividend"

    def test_spinoff(self) -> None:
        row = {
            "ex_dividend_date": "2024-04-10",
            "ticker": "ABC",
            "cash_amount": None,
            "dividend_type": "SP",
        }
        action = PolygonCorpActionsClient._dividend_to_corp_action(row)
        assert action is not None
        assert action.action_type == "spinoff"

    def test_unknown_dividend_type_returns_none(self) -> None:
        """Unmapped dividend_type → None (defensive; don't pollute silver)."""
        row = {
            "ex_dividend_date": "2024-04-10",
            "ticker": "ABC",
            "cash_amount": 1.0,
            "dividend_type": "ZZ_UNKNOWN",
        }
        assert PolygonCorpActionsClient._dividend_to_corp_action(row) is None

    def test_missing_ex_date_returns_none(self) -> None:
        """No ex_dividend_date → None (can't write a row without identifier)."""
        row = {
            "ticker": "AAPL",
            "cash_amount": 0.82,
            "dividend_type": "CD",
        }
        assert PolygonCorpActionsClient._dividend_to_corp_action(row) is None

    def test_declaration_date_optional(self) -> None:
        """Missing declaration_date → announced_at = None, still valid row."""
        row = {
            "ex_dividend_date": "2020-08-07",
            "ticker": "AAPL",
            "cash_amount": 0.82,
            "dividend_type": "CD",
            # no declaration_date
        }
        action = PolygonCorpActionsClient._dividend_to_corp_action(row)
        assert action is not None
        assert action.announced_at is None

    def test_dividend_type_map_coverage(self) -> None:
        """Sanity: the five known dividend types each map to a DISTINCT
        CorpActionKind.

        Why distinct: collapsing LT/ST under cash_dividend produces
        duplicate-key upsert errors when a fund issues both an ordinary
        cash div AND a capital-gains distribution on the same ex_date
        (real case caught by the TA-5.0 live verification).
        """
        assert _DIVIDEND_TYPE_MAP["CD"] == "cash_dividend"
        assert _DIVIDEND_TYPE_MAP["LT"] == "lt_capital_gain"
        assert _DIVIDEND_TYPE_MAP["ST"] == "st_capital_gain"
        assert _DIVIDEND_TYPE_MAP["SC"] == "stock_dividend"
        assert _DIVIDEND_TYPE_MAP["SP"] == "spinoff"
        # All five mappings should be unique destinations.
        assert len(set(_DIVIDEND_TYPE_MAP.values())) == 5


# ─────────────────────────────────────────────────────────────────────
# Client guardrails
# ─────────────────────────────────────────────────────────────────────


class TestClientGuards:
    def test_empty_api_key_raises(self) -> None:
        with pytest.raises(ValueError, match="non-empty api_key"):
            PolygonCorpActionsClient(api_key="")

    def test_constructor_accepts_explicit_key(self) -> None:
        c = PolygonCorpActionsClient(api_key="test-key", sleep_between_requests_s=0.0)
        assert c._api_key == "test-key"
        assert c._sleep_s == 0.0


# ─────────────────────────────────────────────────────────────────────
# Bronze ingest — Arrow serialization
# ─────────────────────────────────────────────────────────────────────


class TestBronzeIngestArrowSerialization:
    """Verify CorpAction → PyArrow Table preserves all columns including
    the audit metadata."""

    def test_empty_list_produces_empty_arrow(self) -> None:
        arrow = PolygonCorpActionsIngest._actions_to_arrow(
            [], ingestion_run_id="r1",
        )
        assert arrow.num_rows == 0
        assert arrow.schema.equals(_CORP_ACTIONS_ARROW)

    def test_single_action_all_columns_populated(self) -> None:
        actions = [
            CorpAction(
                symbol="AAPL",
                ex_date=date(2020, 8, 31),
                action_type="split",
                factor=4.0,
                source_provider="polygon",
            )
        ]
        arrow = PolygonCorpActionsIngest._actions_to_arrow(
            actions,
            ingestion_run_id="r1",
            ingestion_ts=datetime(2026, 5, 17, 0, 0, tzinfo=timezone.utc),
        )
        assert arrow.num_rows == 1
        # All 10 columns present (CV1: includes raw_payload column)
        assert set(arrow.column_names) == set(_CORP_ACTIONS_ARROW.names)
        assert "raw_payload" in arrow.column_names
        assert arrow["raw_payload"][0].as_py() is None
        # Audit metadata stamped
        row = {col: arrow[col][0].as_py() for col in arrow.column_names}
        assert row["ingestion_run_id"] == "r1"
        assert row["ingestion_ts"] == datetime(2026, 5, 17, 0, 0, tzinfo=timezone.utc)
        assert row["symbol"] == "AAPL"
        assert row["action_type"] == "split"
        assert row["factor"] == 4.0
        assert row["cash_amount"] is None

    def test_mixed_splits_and_dividends(self) -> None:
        actions = [
            CorpAction(symbol="AAPL", ex_date=date(2020, 8, 31), action_type="split", factor=4.0),
            CorpAction(symbol="MSFT", ex_date=date(2020, 8, 19), action_type="cash_dividend", cash_amount=0.51),
        ]
        arrow = PolygonCorpActionsIngest._actions_to_arrow(
            actions, ingestion_run_id="r1",
        )
        assert arrow.num_rows == 2


class TestDedupeActions:
    """`_dedupe_actions` collapses same-day same-symbol same-kind rows.

    Regression: TA-5.0 live verification (2026-05-17) found Polygon's
    /dividends endpoint returns multiple cash dividends on the same
    ex_date for some tickers (regular + special). PyIceberg upsert
    rejects duplicate-key sources, so we must dedupe at ingest time.
    """

    def test_no_duplicates_passthrough(self) -> None:
        actions = [
            CorpAction(symbol="AAPL", ex_date=date(2020, 8, 31),
                       action_type="split", factor=4.0),
        ]
        out, n = PolygonCorpActionsIngest._dedupe_actions(actions)
        assert len(out) == 1
        assert n == 0

    def test_empty_passthrough(self) -> None:
        out, n = PolygonCorpActionsIngest._dedupe_actions([])
        assert out == [] and n == 0

    def test_duplicate_cash_dividends_summed(self) -> None:
        """Regular + special cash dividend on same ex_date → one row
        with combined cash_amount."""
        actions = [
            CorpAction(symbol="CIVI", ex_date=date(2024, 6, 12),
                       action_type="cash_dividend", cash_amount=1.0),
            CorpAction(symbol="CIVI", ex_date=date(2024, 6, 12),
                       action_type="cash_dividend", cash_amount=0.5),
        ]
        out, n = PolygonCorpActionsIngest._dedupe_actions(actions)
        assert len(out) == 1
        assert n == 1
        assert out[0].cash_amount == 1.5
        assert out[0].symbol == "CIVI"

    def test_announced_at_takes_latest(self) -> None:
        """When duplicates have different announced_at, keep the latest
        (most up-to-date filing)."""
        actions = [
            CorpAction(symbol="X", ex_date=date(2024, 6, 12),
                       action_type="cash_dividend", cash_amount=0.5,
                       announced_at=datetime(2024, 5, 1, tzinfo=timezone.utc)),
            CorpAction(symbol="X", ex_date=date(2024, 6, 12),
                       action_type="cash_dividend", cash_amount=0.3,
                       announced_at=datetime(2024, 5, 20, tzinfo=timezone.utc)),
        ]
        out, _ = PolygonCorpActionsIngest._dedupe_actions(actions)
        assert out[0].announced_at == datetime(2024, 5, 20, tzinfo=timezone.utc)

    def test_different_action_types_not_collapsed(self) -> None:
        """Same symbol+date but different action_type → kept separate."""
        actions = [
            CorpAction(symbol="FUND", ex_date=date(2024, 12, 30),
                       action_type="cash_dividend", cash_amount=0.10),
            CorpAction(symbol="FUND", ex_date=date(2024, 12, 30),
                       action_type="lt_capital_gain", cash_amount=2.50),
            CorpAction(symbol="FUND", ex_date=date(2024, 12, 30),
                       action_type="st_capital_gain", cash_amount=0.50),
        ]
        out, n = PolygonCorpActionsIngest._dedupe_actions(actions)
        assert len(out) == 3
        assert n == 0

    def test_three_way_duplicate_collapsed(self) -> None:
        """Three duplicates → one row with all summed."""
        actions = [
            CorpAction(symbol="X", ex_date=date(2024, 1, 1),
                       action_type="cash_dividend", cash_amount=0.1),
            CorpAction(symbol="X", ex_date=date(2024, 1, 1),
                       action_type="cash_dividend", cash_amount=0.2),
            CorpAction(symbol="X", ex_date=date(2024, 1, 1),
                       action_type="cash_dividend", cash_amount=0.3),
        ]
        out, n = PolygonCorpActionsIngest._dedupe_actions(actions)
        assert len(out) == 1
        assert n == 2
        assert abs(out[0].cash_amount - 0.6) < 1e-9


# ─────────────────────────────────────────────────────────────────────
# Silver build — merge with precedence
# ─────────────────────────────────────────────────────────────────────


def _make_silver_arrow(rows: list[dict]) -> pa.Table:
    """Helper: build an Arrow Table matching the silver schema."""
    arrays = {col: [r.get(col) for r in rows] for col in _SILVER_CORP_ACTIONS_ARROW.names}
    return pa.Table.from_pydict(arrays, schema=_SILVER_CORP_ACTIONS_ARROW)


class TestSilverMergePrecedence:
    """`_merge_with_precedence` resolves conflicts via the first-with-row-wins rule."""

    def test_polygon_wins_when_both_have_same_action(self) -> None:
        polygon = _make_silver_arrow([{
            "symbol": "AAPL",
            "ex_date": date(2020, 8, 31),
            "action_type": "split",
            "factor": 4.0,
            "cash_amount": None,
            "announced_at": None,
            "source_provider": "polygon",
            "ingestion_ts": None,
            "ingestion_run_id": None,
        }])
        schwab = _make_silver_arrow([{
            "symbol": "AAPL",
            "ex_date": date(2020, 8, 31),
            "action_type": "split",
            "factor": 3.999,
            "cash_amount": None,
            "announced_at": None,
            "source_provider": "schwab",
            "ingestion_ts": None,
            "ingestion_run_id": None,
        }])
        merged = SilverCorpActionsBuild._merge_with_precedence([
            ("polygon", polygon),
            ("schwab", schwab),
        ])
        assert merged.num_rows == 1
        row = merged.to_pylist()[0]
        assert row["factor"] == 4.0
        assert row["source_provider"] == "polygon"

    def test_schwab_fills_when_polygon_missing(self) -> None:
        polygon = _make_silver_arrow([])
        schwab = _make_silver_arrow([{
            "symbol": "GOOG",
            "ex_date": date(2022, 7, 18),
            "action_type": "split",
            "factor": 20.0,
            "cash_amount": None,
            "announced_at": None,
            "source_provider": "schwab",
            "ingestion_ts": None,
            "ingestion_run_id": None,
        }])
        merged = SilverCorpActionsBuild._merge_with_precedence([
            ("polygon", polygon),
            ("schwab", schwab),
        ])
        assert merged.num_rows == 1
        assert merged.to_pylist()[0]["source_provider"] == "schwab"

    def test_disjoint_inputs_combine(self) -> None:
        """Different (symbol, ex_date) — both kept."""
        polygon = _make_silver_arrow([{
            "symbol": "AAPL", "ex_date": date(2020, 8, 31), "action_type": "split",
            "factor": 4.0, "cash_amount": None, "announced_at": None,
            "source_provider": "polygon", "ingestion_ts": None, "ingestion_run_id": None,
        }])
        schwab = _make_silver_arrow([{
            "symbol": "MSFT", "ex_date": date(2020, 1, 1), "action_type": "cash_dividend",
            "factor": None, "cash_amount": 0.51, "announced_at": None,
            "source_provider": "schwab", "ingestion_ts": None, "ingestion_run_id": None,
        }])
        merged = SilverCorpActionsBuild._merge_with_precedence([
            ("polygon", polygon), ("schwab", schwab),
        ])
        assert merged.num_rows == 2
        symbols = sorted(r["symbol"] for r in merged.to_pylist())
        assert symbols == ["AAPL", "MSFT"]

    def test_empty_input_returns_empty_with_schema(self) -> None:
        """No providers contributed → empty Arrow, but the schema is preserved."""
        merged = SilverCorpActionsBuild._merge_with_precedence([])
        assert merged.num_rows == 0
        assert merged.schema.equals(_SILVER_CORP_ACTIONS_ARROW)

    def test_same_action_type_different_dates_both_kept(self) -> None:
        """Identifier is (symbol, ex_date, action_type). Different dates for
        the same symbol+action both go through."""
        polygon = _make_silver_arrow([
            {"symbol": "AAPL", "ex_date": date(2020, 8, 7), "action_type": "cash_dividend",
             "factor": None, "cash_amount": 0.82, "announced_at": None,
             "source_provider": "polygon", "ingestion_ts": None, "ingestion_run_id": None},
            {"symbol": "AAPL", "ex_date": date(2020, 11, 6), "action_type": "cash_dividend",
             "factor": None, "cash_amount": 0.205, "announced_at": None,
             "source_provider": "polygon", "ingestion_ts": None, "ingestion_run_id": None},
        ])
        merged = SilverCorpActionsBuild._merge_with_precedence([("polygon", polygon)])
        assert merged.num_rows == 2


class TestSilverBuildRestamp:
    """`_restamp_ingestion` replaces bronze's audit metadata with silver's."""

    def test_restamp_overrides_run_id(self) -> None:
        bronze_like = _make_silver_arrow([{
            "symbol": "AAPL", "ex_date": date(2020, 8, 31), "action_type": "split",
            "factor": 4.0, "cash_amount": None, "announced_at": None,
            "source_provider": "polygon",
            "ingestion_ts": datetime(2026, 5, 16, tzinfo=timezone.utc),
            "ingestion_run_id": "bronze-run-old",
        }])
        out = SilverCorpActionsBuild._restamp_ingestion(bronze_like, run_id="silver-run-new")
        row = out.to_pylist()[0]
        assert row["ingestion_run_id"] == "silver-run-new"
        # ingestion_ts is now "this build's now" — sanity check it's recent.
        assert row["ingestion_ts"] is not None
        # Other columns untouched
        assert row["symbol"] == "AAPL"
        assert row["factor"] == 4.0

    def test_restamp_on_empty_arrow_is_noop(self) -> None:
        empty = _make_silver_arrow([])
        out = SilverCorpActionsBuild._restamp_ingestion(empty, run_id="r1")
        assert out.num_rows == 0
        assert out.schema.equals(_SILVER_CORP_ACTIONS_ARROW)


# ─────────────────────────────────────────────────────────────────────
# Configuration parsing
# ─────────────────────────────────────────────────────────────────────


class TestSilverBuildSettingsParsing:
    def test_explicit_precedence_list(self) -> None:
        """When precedence is passed at construction, it's used as-is."""
        b = SilverCorpActionsBuild(provider_precedence=["polygon", "schwab"])
        assert b._get_provider_precedence() == ["polygon", "schwab"]

    def test_from_settings_parses_csv(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """from_settings reads SILVER_PROVIDER_PRECEDENCE comma-list."""
        from app.config import settings

        # Direct attr-set since settings is a dataclass-like singleton
        monkeypatch.setattr(
            settings, "silver_provider_precedence", "polygon, schwab, custom",
        )
        b = SilverCorpActionsBuild.from_settings()
        assert b._get_provider_precedence() == ["polygon", "schwab", "custom"]

    def test_from_settings_empty_string_raises(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.config import settings

        monkeypatch.setattr(settings, "silver_provider_precedence", "")
        with pytest.raises(ValueError, match="silver_provider_precedence is empty"):
            SilverCorpActionsBuild.from_settings()


# ─────────────────────────────────────────────────────────────────────
# CorpActionsReader — read service for silver.corp_actions
# ─────────────────────────────────────────────────────────────────────


class TestCorpActionsReader:
    def test_empty_symbol_returns_empty_response(self) -> None:
        from app.services.readers.corp_actions_reader import CorpActionsReader

        r = CorpActionsReader()
        resp = r.get_corp_actions("")
        assert resp.count == 0
        assert resp.actions == []
        assert resp.snapshot_id is None

    def test_whitespace_symbol_returns_empty(self) -> None:
        from app.services.readers.corp_actions_reader import CorpActionsReader

        r = CorpActionsReader()
        resp = r.get_corp_actions("   ")
        assert resp.count == 0

    def test_missing_table_returns_empty_gracefully(self) -> None:
        """Reader handles 'silver.corp_actions doesn't exist yet' without
        raising — important because silver may be unbuilt in fresh setups."""
        from unittest.mock import patch
        from app.services.readers.corp_actions_reader import CorpActionsReader

        r = CorpActionsReader()
        # Patch _get_table to simulate a load failure.
        with patch.object(r, "_get_table", side_effect=RuntimeError("no table")):
            resp = r.get_corp_actions("AAPL")
        assert resp.count == 0
        assert resp.actions == []
        assert resp.symbol == "AAPL"

    def test_arrow_to_actions_sorts_by_ex_date_then_kind(self) -> None:
        """Output is sorted (ex_date, action_type) for deterministic
        downstream behavior."""
        from app.services.readers.corp_actions_reader import CorpActionsReader
        from datetime import date as _date

        # Build an Arrow Table by hand to test the helper directly.
        rows = [
            {"symbol": "AAPL", "ex_date": _date(2020, 8, 31), "action_type": "split",
             "factor": 4.0, "cash_amount": None, "announced_at": None,
             "source_provider": "polygon", "ingestion_ts": None, "ingestion_run_id": None},
            {"symbol": "AAPL", "ex_date": _date(2020, 8, 7), "action_type": "cash_dividend",
             "factor": None, "cash_amount": 0.82, "announced_at": None,
             "source_provider": "polygon", "ingestion_ts": None, "ingestion_run_id": None},
        ]
        arrow = _make_silver_arrow(rows)
        actions = CorpActionsReader._arrow_to_actions(arrow)
        assert len(actions) == 2
        # Sorted: 2020-08-07 first, then 2020-08-31
        assert actions[0].ex_date == _date(2020, 8, 7)
        assert actions[1].ex_date == _date(2020, 8, 31)

    def test_symbol_uppercased(self) -> None:
        """Input symbol is normalized to uppercase before query."""
        from unittest.mock import patch
        from app.services.readers.corp_actions_reader import CorpActionsReader

        r = CorpActionsReader()
        with patch.object(r, "_get_table", side_effect=RuntimeError("no table")):
            resp = r.get_corp_actions("aapl")
        assert resp.symbol == "AAPL"


# ─────────────────────────────────────────────────────────────────────
# Year-chunking regression — `backfill_full_history` must iterate year
# by year, NEVER one-shot the whole window.
# ─────────────────────────────────────────────────────────────────────


class TestBackfillFullHistoryYearChunking:
    """Locks in the TA-5.0 OOM fix: `backfill_full_history` MUST call
    Polygon's collect_splits / collect_dividends once per calendar
    year, not once for the whole window.

    **Regression context (2026-05-17):** pulling 23 years of dividends
    in one shot held ~3M rows in memory at once → silent OOM crash on
    residential hardware. The fix chunks the loop by calendar year so
    each upsert + each pagination window holds ≤ ~200K rows.

    If a future refactor removes the chunking (e.g. "let's just call
    collect_dividends once with since=2003 until=today"), THIS TEST
    BREAKS — keeping the silent-OOM trap from coming back.
    """

    @pytest.mark.asyncio
    async def test_chunks_call_collect_per_year(self) -> None:
        """The backfill must call collect_splits + collect_dividends
        exactly once per calendar year in the window."""
        from datetime import date as _date
        from unittest.mock import AsyncMock, MagicMock

        client = MagicMock(spec=PolygonCorpActionsClient)
        client.collect_splits = AsyncMock(return_value=[])
        client.collect_dividends = AsyncMock(return_value=[])

        # Fake table — never written to because there are no actions.
        fake_table = MagicMock()

        ingest = PolygonCorpActionsIngest(client=client, table=fake_table)
        await ingest.backfill_full_history(
            since=_date(2021, 1, 4), until=_date(2024, 6, 30),
        )

        # 2021, 2022, 2023, 2024 → 4 years → 4 calls each.
        assert client.collect_splits.await_count == 4, (
            "collect_splits should be called once per calendar year; "
            "got %d calls — has year-chunking been removed?"
            % client.collect_splits.await_count
        )
        assert client.collect_dividends.await_count == 4, (
            "collect_dividends should be called once per calendar year; "
            "got %d calls — has year-chunking been removed?"
            % client.collect_dividends.await_count
        )

        # Confirm each call uses a window inside its own calendar year.
        for call in client.collect_splits.await_args_list:
            kwargs = call.kwargs
            assert kwargs["since"].year == kwargs["until"].year, (
                "year-chunk window crosses calendar years; OOM fix broken"
            )
        for call in client.collect_dividends.await_args_list:
            kwargs = call.kwargs
            assert kwargs["since"].year == kwargs["until"].year, (
                "year-chunk window crosses calendar years; OOM fix broken"
            )

    @pytest.mark.asyncio
    async def test_single_year_window_makes_one_call(self) -> None:
        """A within-one-year window should result in exactly one chunk."""
        from datetime import date as _date
        from unittest.mock import AsyncMock, MagicMock

        client = MagicMock(spec=PolygonCorpActionsClient)
        client.collect_splits = AsyncMock(return_value=[])
        client.collect_dividends = AsyncMock(return_value=[])

        ingest = PolygonCorpActionsIngest(client=client, table=MagicMock())
        await ingest.backfill_full_history(
            since=_date(2024, 3, 1), until=_date(2024, 11, 30),
        )

        assert client.collect_splits.await_count == 1
        assert client.collect_dividends.await_count == 1
        # The single chunk should respect the caller's bounds, not
        # widen to the full year.
        call = client.collect_splits.await_args_list[0]
        assert call.kwargs["since"] == _date(2024, 3, 1)
        assert call.kwargs["until"] == _date(2024, 11, 30)

    @pytest.mark.asyncio
    async def test_clamps_chunk_to_caller_bounds(self) -> None:
        """A multi-year window with partial first/last years should
        clamp the first/last chunk to the caller's bounds, not extend
        to Jan 1 / Dec 31 of those years."""
        from datetime import date as _date
        from unittest.mock import AsyncMock, MagicMock

        client = MagicMock(spec=PolygonCorpActionsClient)
        client.collect_splits = AsyncMock(return_value=[])
        client.collect_dividends = AsyncMock(return_value=[])

        ingest = PolygonCorpActionsIngest(client=client, table=MagicMock())
        await ingest.backfill_full_history(
            since=_date(2023, 6, 15), until=_date(2025, 3, 10),
        )

        # 2023 chunk → since=2023-06-15 (NOT 2023-01-01)
        first_call = client.collect_splits.await_args_list[0]
        assert first_call.kwargs["since"] == _date(2023, 6, 15)
        assert first_call.kwargs["until"] == _date(2023, 12, 31)

        # 2025 chunk → until=2025-03-10 (NOT 2025-12-31)
        last_call = client.collect_splits.await_args_list[-1]
        assert last_call.kwargs["since"] == _date(2025, 1, 1)
        assert last_call.kwargs["until"] == _date(2025, 3, 10)

    def test_upsert_routes_through_chunked_upsert(self) -> None:
        """`_upsert` MUST delegate the actual table write to
        `chunked_upsert` — the shared helper that guards every
        iceberg upsert in the codebase from the PyIceberg multi-col
        predicate-tree SIGBUS.

        **Regression context (2026-05-18):** bronze.polygon_corp_actions
        upsert reliably bus-errored on macOS at ~1,000 rows. We
        bisected the threshold and centralized the fix in
        `app/services/iceberg_safe_upsert.py`. The chunking math is
        pinned in `tests/test_iceberg_safe_upsert.py` — this test
        only pins the DELEGATION (so a refactor that bypasses the
        helper breaks loudly).
        """
        from unittest.mock import MagicMock, patch
        from app.services.ingest.corp_actions import (
            PolygonCorpActionsIngest as _Cls,
        )

        # Build a 1,500-row payload — enough to force chunking via the helper.
        actions = [
            CorpAction(
                symbol=f"SYM{i:04d}", ex_date=date(2024, 1, 1),
                action_type="cash_dividend", cash_amount=0.10,
            )
            for i in range(1500)
        ]
        fake_table = MagicMock()

        with patch(
            "app.services.ingest.corp_actions.chunked_upsert"
        ) as mock_chunked:
            mock_chunked.return_value = MagicMock(
                rows_updated=0, rows_inserted=1500, chunks_committed=4,
            )
            _Cls._upsert(fake_table, actions, ingestion_run_id="test-run")

            # Must be exactly one call to the helper — the helper itself
            # handles chunking internally.
            assert mock_chunked.call_count == 1
            # Bare `.upsert(...)` must NEVER be called from polygon_ingest
            # — delegation must go through chunked_upsert.
            assert fake_table.upsert.call_count == 0
            # The helper got the full deduped payload.
            arrow_arg = mock_chunked.call_args.args[1]
            assert arrow_arg.num_rows == 1500

    @pytest.mark.asyncio
    async def test_summary_aggregates_across_years(self) -> None:
        """Returned summary should aggregate row counts across all
        years, not just the last chunk."""
        from datetime import date as _date
        from unittest.mock import AsyncMock, MagicMock

        # Return 10 fake splits + 100 fake dividends per year.
        fake_split = CorpAction(
            symbol="X", ex_date=_date(2023, 1, 2), action_type="split", factor=2.0,
        )
        fake_div = CorpAction(
            symbol="X", ex_date=_date(2023, 1, 2),
            action_type="cash_dividend", cash_amount=0.1,
        )

        client = MagicMock(spec=PolygonCorpActionsClient)
        client.collect_splits = AsyncMock(return_value=[fake_split] * 10)
        client.collect_dividends = AsyncMock(return_value=[fake_div] * 100)

        # Stub table.upsert to avoid actually writing.
        fake_table = MagicMock()
        fake_table.upsert.return_value = MagicMock(rows_updated=0, rows_inserted=110)

        ingest = PolygonCorpActionsIngest(client=client, table=fake_table)
        result = await ingest.backfill_full_history(
            since=_date(2021, 1, 1), until=_date(2023, 12, 31),
        )

        # 3 years × 10 splits/year + 3 years × 100 dividends/year
        assert result["splits_written"] == 30
        assert result["dividends_written"] == 300
