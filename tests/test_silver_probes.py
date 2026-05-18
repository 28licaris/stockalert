"""
Unit tests for the universal provider-adjustment probe framework.

Tests are network-free — they exercise the registry, the classifier,
and the result types. Live network probes happen via
`scripts/probe_provider_adjustment.py` (operator runbook), not pytest.
"""
from __future__ import annotations

from datetime import date

import pytest

from app.services.silver.probes import (
    DEFAULT_PROBE_SPEC,
    KNOWN_PROBES,
    ProbeResult,
    ProbeSpec,
    ProviderAdjustmentProbe,
    build_all_probes,
    list_registered_probes,
    register_probe,
)
from app.services.silver.probes.base import (
    CLASSIFICATIONS,
    TOLERANCE_DOLLARS,
    TOLERANCE_PCT,
    ExpectedClose,
    classify,
    close_to,
)


# ─────────────────────────────────────────────────────────────────────
# Registry
# ─────────────────────────────────────────────────────────────────────


class TestRegistry:
    def test_polygon_and_schwab_are_registered(self) -> None:
        registered = list_registered_probes()
        assert "polygon" in registered
        assert "schwab" in registered

    def test_build_all_probes_instantiates(self) -> None:
        probes = build_all_probes()
        names = sorted(p.provider_name for p in probes)
        assert names == ["polygon", "schwab"]

    def test_re_register_same_name_raises(self) -> None:
        """The registry rejects duplicate registrations — fail fast."""
        with pytest.raises(ValueError, match="already registered"):
            @register_probe("polygon")
            class Duplicate:
                provider_name = "polygon"
                async def probe(self, spec): return []

    def test_probes_conform_to_protocol(self) -> None:
        """Both registered probes satisfy ProviderAdjustmentProbe."""
        for p in build_all_probes():
            assert isinstance(p, ProviderAdjustmentProbe)
            assert hasattr(p, "provider_name")
            assert hasattr(p, "probe")


# ─────────────────────────────────────────────────────────────────────
# Known-probe library
# ─────────────────────────────────────────────────────────────────────


class TestKnownProbes:
    def test_default_probe_is_in_library(self) -> None:
        assert DEFAULT_PROBE_SPEC is KNOWN_PROBES["aapl_2020_4for1"]

    def test_all_known_probes_have_consistent_math(self) -> None:
        """For each known probe, split_adjusted_close ≈ raw_close / factor."""
        for name, spec in KNOWN_PROBES.items():
            implied = spec.expected_pre.raw / spec.split_factor
            assert abs(implied - spec.expected_pre.split_adjusted) < 0.01, (
                f"{name}: split_adjusted={spec.expected_pre.split_adjusted} "
                f"but raw/factor={implied:.4f}"
            )

    def test_post_split_raw_equals_split_adjusted(self) -> None:
        """Post-split, raw and split-adjusted converge (no further adjustment)."""
        for name, spec in KNOWN_PROBES.items():
            assert spec.expected_post.raw == spec.expected_post.split_adjusted, (
                f"{name}: post-split raw ≠ split_adjusted"
            )

    def test_pre_split_date_before_post_split_date(self) -> None:
        for name, spec in KNOWN_PROBES.items():
            assert spec.pre_split_date < spec.post_split_date, name

    def test_at_least_five_probes(self) -> None:
        """We started with 5 (AAPL/NVDA/AMZN/GOOGL/TSLA). Asserting count
        catches accidental deletions in PRs."""
        assert len(KNOWN_PROBES) >= 5


# ─────────────────────────────────────────────────────────────────────
# Classifier
# ─────────────────────────────────────────────────────────────────────


class TestClassifier:
    def test_close_to_within_dollar_tolerance(self) -> None:
        assert close_to(124.81, 124.81)
        assert close_to(124.81 + TOLERANCE_DOLLARS - 0.01, 124.81)
        assert not close_to(124.81 + TOLERANCE_DOLLARS + 1.0, 124.81)

    def test_close_to_within_percent_tolerance_for_large_values(self) -> None:
        """For $1208.88, 0.5% = ~$6 — much larger than $0.50, so percent wins."""
        assert close_to(1208.88 * 1.001, 1208.88)
        assert not close_to(1208.88 * 1.02, 1208.88)

    def test_close_to_none_is_false(self) -> None:
        assert not close_to(None, 100.0)

    def test_close_to_zero_expected_is_false(self) -> None:
        assert not close_to(0.0, 0.0)
        assert not close_to(1.0, 0.0)

    def test_classify_raw(self) -> None:
        expected = ExpectedClose(raw=499.23, split_adjusted=124.81)
        assert classify(499.23, expected) == "raw"
        assert classify(499.0, expected) == "raw"   # within dollar tolerance

    def test_classify_split_adjusted(self) -> None:
        expected = ExpectedClose(raw=499.23, split_adjusted=124.81)
        assert classify(124.81, expected) == "split_adjusted"
        assert classify(124.85, expected) == "split_adjusted"

    def test_classify_other(self) -> None:
        """Value matching neither = 'other'."""
        expected = ExpectedClose(raw=499.23, split_adjusted=124.81)
        assert classify(300.0, expected) == "other"

    def test_classify_no_data(self) -> None:
        expected = ExpectedClose(raw=499.23, split_adjusted=124.81)
        assert classify(None, expected) == "no_data"

    def test_classification_strings_are_valid(self) -> None:
        """All CLASSIFICATIONS constants are reachable from classify()."""
        # raw, split_adjusted, no_data are covered above; other is covered.
        # error only comes from probe.probe() catching exceptions.
        assert {"raw", "split_adjusted", "other", "no_data", "error"} <= CLASSIFICATIONS


# ─────────────────────────────────────────────────────────────────────
# Probe error handling — probes never raise
# ─────────────────────────────────────────────────────────────────────


class TestProbeErrorHandling:
    """Both probes must produce AuditResult(error=...) on bad input rather
    than raising — the runner relies on this for a complete report."""

    @pytest.mark.asyncio
    async def test_polygon_probe_with_no_api_key(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.config import settings
        from app.services.silver.probes.polygon import PolygonAdjustmentProbe

        monkeypatch.setattr(settings, "polygon_api_key", "")
        probe = PolygonAdjustmentProbe()
        results = await probe.probe(DEFAULT_PROBE_SPEC)
        # Should produce per-endpoint error/skip rows, never raise.
        assert isinstance(results, list)
        assert len(results) > 0
        # The REST endpoints should be error (no API key).
        rest_endpoints = [r for r in results if "polygon_rest" in r.endpoint]
        assert all(r.classification == "error" for r in rest_endpoints)
        assert all(r.error and "POLYGON_API_KEY" in r.error for r in rest_endpoints)

    @pytest.mark.asyncio
    async def test_schwab_probe_with_no_credentials(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from app.config import settings
        from app.services.silver.probes.schwab import SchwabAdjustmentProbe

        monkeypatch.setattr(settings, "schwab_client_id", "")
        monkeypatch.setattr(settings, "schwab_client_secret", "")
        # Don't try to re-set the refresh token method; setting the inputs to empty is enough.

        probe = SchwabAdjustmentProbe()
        results = await probe.probe(DEFAULT_PROBE_SPEC)
        assert isinstance(results, list)
        assert len(results) > 0
        assert all(r.classification == "error" for r in results)


# ─────────────────────────────────────────────────────────────────────
# ProbeSpec / ProbeResult dataclass shapes
# ─────────────────────────────────────────────────────────────────────


class TestProbeShapes:
    def test_probe_result_required_fields(self) -> None:
        r = ProbeResult(
            provider="polygon",
            endpoint="test",
            probe_date=date(2020, 1, 1),
            returned_close=100.0,
            classification="raw",
            matches_raw=True,
            matches_split_adjusted=False,
        )
        assert r.error is None
        assert r.provider == "polygon"

    def test_probe_spec_is_frozen(self) -> None:
        """ProbeSpec is a frozen dataclass — immutable spec library."""
        with pytest.raises(Exception):  # FrozenInstanceError or AttributeError
            DEFAULT_PROBE_SPEC.symbol = "MUTATED"  # type: ignore
