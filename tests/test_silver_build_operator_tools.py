"""
Tests for TA-5.1.7 operator tooling:
  - scripts/preflight_silver_build.py
  - scripts/verify_silver_build.py

These scripts touch the real Iceberg catalog + ClickHouse in
production. The tests here exercise the unit-testable helpers
(result dataclasses, symbol resolution, gap-outlier classification,
cross-check logic) without requiring real lake infrastructure —
production wiring is the operator's responsibility to verify by
actually running the scripts.
"""
from __future__ import annotations

import importlib.util
import sys
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

_HERE = Path(__file__).resolve().parent
_SCRIPTS = _HERE.parent / "scripts"


def _load_script(name: str):
    """Import a script file as a module (scripts/ aren't on sys.path)."""
    path = _SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"scripts.{name}", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[f"scripts.{name}"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def preflight():
    return _load_script("preflight_silver_build")


@pytest.fixture(scope="module")
def build_cli():
    """The scripts/run_silver_ohlcv_build.py operator CLI."""
    return _load_script("run_silver_ohlcv_build")


# ─────────────────────────────────────────────────────────────────────
# Build CLI symbol resolution — regression for "active" not being a CSV
# ─────────────────────────────────────────────────────────────────────


class TestBuildCliResolveSymbols:
    """Regression: an earlier version of the CLI's local _resolve_symbols
    didn't know about 'active' and treated it as a single-symbol CSV,
    starting an empty backfill. Fixed by delegating to the universe
    service (same path the nightlies use)."""

    def test_seed_returns_seed_symbols(self, build_cli) -> None:
        from app.data.seed_universe import SEED_SYMBOLS

        syms = build_cli._resolve_symbols("seed")
        assert set(syms) == set(SEED_SYMBOLS)

    def test_active_reads_stream_universe(self, build_cli) -> None:
        """`active` resolves to the canonical `stream_universe` table.
        Critically, NOT a literal single symbol 'ACTIVE'. Per
        docs/standards/data/symbol_lifecycle.md, watchlists no longer
        contribute to the universe — stream_universe is the source."""
        from unittest.mock import patch

        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            return_value={"NEW_STREAM_SYM"},
        ):
            syms = build_cli._resolve_symbols("active")
        assert syms == ["NEW_STREAM_SYM"]
        # Critically not parsed as a literal symbol.
        assert syms != ["ACTIVE"]

    def test_empty_string_defaults_to_active(self, build_cli) -> None:
        """Empty/None now default to `active` (stream_universe), not seed.
        Pre-FE-CONTRACTS-4-final, empty meant SEED_SYMBOLS; the new
        canonical default is stream_universe."""
        from unittest.mock import patch

        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            return_value={"PG"},
        ):
            assert build_cli._resolve_symbols("") == ["PG"]

    def test_none_defaults_to_active(self, build_cli) -> None:
        from unittest.mock import patch

        with patch(
            "app.services.stream.stream_service.list_active_symbols",
            return_value={"PG"},
        ):
            assert build_cli._resolve_symbols(None) == ["PG"]

    def test_csv_uppercased(self, build_cli) -> None:
        syms = build_cli._resolve_symbols("aapl,nvda,MSFT")
        assert syms == ["AAPL", "NVDA", "MSFT"]


@pytest.fixture(scope="module")
def verify():
    return _load_script("verify_silver_build")


# ─────────────────────────────────────────────────────────────────────
# preflight — CheckResult + parser + report rendering
# ─────────────────────────────────────────────────────────────────────


class TestPreflightCheckResult:
    def test_glyph_per_status(self, preflight) -> None:
        ok = preflight.CheckResult(name="x", status="ok", message="m")
        warn = preflight.CheckResult(name="x", status="warn", message="m")
        fail = preflight.CheckResult(name="x", status="fail", message="m")
        assert "OK" in ok.glyph
        assert "WARN" in warn.glyph
        assert "FAIL" in fail.glyph


class TestPreflightParser:
    def test_default_day_is_yesterday(self, preflight) -> None:
        parser = preflight._build_parser()
        ns = parser.parse_args([])
        from datetime import datetime, timedelta, timezone
        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        assert ns.day == yesterday

    def test_explicit_symbol_and_day(self, preflight) -> None:
        parser = preflight._build_parser()
        ns = parser.parse_args(["--symbol", "NVDA", "--day", "2024-06-10"])
        assert ns.symbol == "NVDA"
        assert ns.day == date(2024, 6, 10)


class TestPreflightOrchestration:
    """When the catalog is unreachable, every downstream check is
    marked fail/skip — none are actually executed."""

    def test_catalog_fail_skips_downstream(
        self, preflight, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        catalog_fail = preflight.CheckResult(
            name="catalog_reachable", status="fail",
            message="boom", detail={},
        )
        monkeypatch.setattr(
            preflight, "check_catalog_reachable", lambda: catalog_fail,
        )
        # If catalog fails, the orchestrator MUST NOT call the
        # downstream checks. Patch them to throw if invoked.
        for fname in (
            "check_bronze_minute_tables",
            "check_silver_corp_actions",
            "check_silver_tables_creatable",
            "check_end_to_end_slice",
            "check_silver_readback",
            "check_ingestion_runs_recorded",
        ):
            monkeypatch.setattr(
                preflight, fname,
                lambda *_a, **_kw: pytest.fail(
                    f"{fname} should not have been called",
                ),
            )

        results = preflight.run_all_checks("AAPL", date(2024, 6, 10))
        assert results[0].status == "fail"
        # Everything else marked fail (skipped).
        assert all(r.status == "fail" for r in results[1:])
        assert all("skipped" in r.message for r in results[1:])

    def test_full_pass_orchestration(
        self, preflight, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """All seven checks return ok → run_all_checks returns 7 OK results."""
        ok = preflight.CheckResult(
            name="catalog_reachable", status="ok", message="ok",
        )
        for fname in (
            "check_catalog_reachable",
            "check_bronze_minute_tables",
            "check_silver_corp_actions",
            "check_silver_tables_creatable",
            "check_end_to_end_slice",
            "check_silver_readback",
            "check_ingestion_runs_recorded",
        ):
            monkeypatch.setattr(
                preflight, fname,
                lambda *_a, fname=fname, **_kw: preflight.CheckResult(
                    name=fname, status="ok", message="ok",
                ),
            )
        results = preflight.run_all_checks("AAPL", date(2024, 6, 10))
        assert len(results) == 7
        assert all(r.status == "ok" for r in results)


# ─────────────────────────────────────────────────────────────────────
# verify — symbol resolution + parser + quality-outlier classification
# ─────────────────────────────────────────────────────────────────────


class TestVerifyResolveSymbols:
    def test_none_returns_seed(self, verify) -> None:
        from app.data.seed_universe import SEED_SYMBOLS

        symbols = verify._resolve_symbols(None)
        assert set(symbols) == set(SEED_SYMBOLS)

    def test_empty_returns_seed(self, verify) -> None:
        from app.data.seed_universe import SEED_SYMBOLS

        symbols = verify._resolve_symbols("")
        assert set(symbols) == set(SEED_SYMBOLS)

    def test_csv_uppercased(self, verify) -> None:
        assert verify._resolve_symbols("aapl,nvda") == ["AAPL", "NVDA"]


class TestVerifyParser:
    def test_default_window_is_last_7_days(self, verify) -> None:
        parser = verify._build_parser()
        ns = parser.parse_args([])
        from datetime import datetime, timedelta, timezone

        yesterday = datetime.now(timezone.utc).date() - timedelta(days=1)
        assert ns.until == yesterday
        assert (yesterday - ns.since).days == 7

    def test_explicit_window(self, verify) -> None:
        parser = verify._build_parser()
        ns = parser.parse_args([
            "--since", "2024-06-01", "--until", "2024-06-30",
        ])
        assert ns.since == date(2024, 6, 1)
        assert ns.until == date(2024, 6, 30)


class TestVerifyQualityClassification:
    """gather_quality_metrics should populate findings based on each
    BarQualityRow's gap_count / disagreement_count / actual_bars."""

    def test_gap_outlier_caught(self, verify) -> None:
        from app.services.readers.schemas import BarQualityResponse, BarQualityRow

        # A weekday with too many gaps gets flagged.
        bad_row = BarQualityRow(
            symbol="AAPL", date=date(2024, 6, 10),  # Monday
            expected_bars=390, actual_bars=300,
            gap_count=10, max_gap_minutes=15,
            providers_seen=["polygon"], disagreement_count=0,
            backfill_attempts=0,
        )
        good_row = BarQualityRow(
            symbol="AAPL", date=date(2024, 6, 11),
            expected_bars=390, actual_bars=388,
            gap_count=2, max_gap_minutes=3,
            providers_seen=["polygon", "schwab"], disagreement_count=0,
            backfill_attempts=0,
        )
        resp = BarQualityResponse(
            symbol="AAPL", since=None, until=None, snapshot_id=None,
            rows=[bad_row, good_row], count=2,
        )

        fake_reader = MagicMock()
        fake_reader.get_bar_quality.return_value = resp
        with patch.object(
            verify, "SilverOhlcvReader", create=True,
        ):
            # Re-import path — instead, patch from_settings on the
            # already-imported module.
            pass
        # Patch the from_settings classmethod via the module's attr.
        with patch(
            "app.services.readers.silver_ohlcv_reader.SilverOhlcvReader.from_settings",
            return_value=fake_reader,
        ):
            findings = verify.VerificationFindings()
            verify.gather_quality_metrics(
                ["AAPL"], date(2024, 6, 10), date(2024, 6, 11),
                max_gap_count=5, max_gap_minutes=10,
                findings=findings,
            )

        assert findings.total_quality_rows == 2
        assert len(findings.cells_with_gap_outlier) == 1
        assert findings.cells_with_gap_outlier[0]["symbol"] == "AAPL"
        assert findings.cells_with_gap_outlier[0]["gap_count"] == 10

    def test_disagreement_caught(self, verify) -> None:
        from app.services.readers.schemas import BarQualityResponse, BarQualityRow

        row = BarQualityRow(
            symbol="NVDA", date=date(2024, 6, 10),
            expected_bars=390, actual_bars=389,
            gap_count=0, max_gap_minutes=0,
            providers_seen=["polygon", "schwab"], disagreement_count=4,
            backfill_attempts=0,
        )
        resp = BarQualityResponse(
            symbol="NVDA", since=None, until=None, snapshot_id=None,
            rows=[row], count=1,
        )
        fake_reader = MagicMock()
        fake_reader.get_bar_quality.return_value = resp
        with patch(
            "app.services.readers.silver_ohlcv_reader.SilverOhlcvReader.from_settings",
            return_value=fake_reader,
        ):
            findings = verify.VerificationFindings()
            verify.gather_quality_metrics(
                ["NVDA"], date(2024, 6, 10), date(2024, 6, 10),
                max_gap_count=5, max_gap_minutes=10,
                findings=findings,
            )

        assert len(findings.cells_with_disagreement) == 1
        assert findings.cells_with_disagreement[0]["disagreement_count"] == 4

    def test_zero_actual_bars_on_weekday_flagged(self, verify) -> None:
        """Saturday/Sunday with 0 bars is normal; Monday is suspect."""
        from app.services.readers.schemas import BarQualityResponse, BarQualityRow

        sat_row = BarQualityRow(
            symbol="AAPL", date=date(2024, 6, 8),  # Saturday
            expected_bars=390, actual_bars=0,
            gap_count=0, max_gap_minutes=0,
            providers_seen=[], disagreement_count=0,
            backfill_attempts=0,
        )
        mon_row = BarQualityRow(
            symbol="AAPL", date=date(2024, 6, 10),  # Monday
            expected_bars=390, actual_bars=0,
            gap_count=0, max_gap_minutes=0,
            providers_seen=[], disagreement_count=0,
            backfill_attempts=0,
        )
        resp = BarQualityResponse(
            symbol="AAPL", since=None, until=None, snapshot_id=None,
            rows=[sat_row, mon_row], count=2,
        )
        fake_reader = MagicMock()
        fake_reader.get_bar_quality.return_value = resp
        with patch(
            "app.services.readers.silver_ohlcv_reader.SilverOhlcvReader.from_settings",
            return_value=fake_reader,
        ):
            findings = verify.VerificationFindings()
            verify.gather_quality_metrics(
                ["AAPL"], date(2024, 6, 8), date(2024, 6, 10),
                max_gap_count=5, max_gap_minutes=10,
                findings=findings,
            )

        # Saturday excluded; Monday flagged.
        assert len(findings.zero_actual_bar_cells) == 1
        assert findings.zero_actual_bar_cells[0]["date"] == "2024-06-10"


class TestVerifyHasIssues:
    """VerificationFindings.has_issues toggles on any outlier."""

    def test_clean_findings(self, verify) -> None:
        f = verify.VerificationFindings()
        assert f.has_issues is False

    def test_gap_outlier_triggers(self, verify) -> None:
        f = verify.VerificationFindings()
        f.cells_with_gap_outlier.append({"x": 1})
        assert f.has_issues is True

    def test_disagreement_triggers(self, verify) -> None:
        f = verify.VerificationFindings()
        f.cells_with_disagreement.append({"x": 1})
        assert f.has_issues is True

    def test_zero_bars_triggers(self, verify) -> None:
        f = verify.VerificationFindings()
        f.zero_actual_bar_cells.append({"x": 1})
        assert f.has_issues is True

    def test_sample_failure_triggers(self, verify) -> None:
        f = verify.VerificationFindings()
        f.sample_failures.append({"x": 1})
        assert f.has_issues is True
