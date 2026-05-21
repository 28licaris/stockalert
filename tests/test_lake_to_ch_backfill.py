"""
Tests for LakeToChBackfill (TA-5.3.1) — silver → ClickHouse path.

Verifies the new canonical fast path: read silver.ohlcv_1m → translate
to CH row dicts → bulk-insert into ClickHouse ohlcv_1m.

Uses a stub reader + a captured insert_bars_batch so the test
exercises the translation + orchestration without needing real CH /
Iceberg.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

import pytest

from app.services.ingest.lake_to_ch_backfill import (
    DEFAULT_BACKFILL_DAYS,
    LakeToChBackfill,
    LakeToChBackfillResult,
)
from app.services.readers.schemas import SilverBarsResponse
from app.services.equities.models import SilverBar


# ─────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────


def _silver_bar(
    symbol: str,
    ts: datetime,
    *,
    close: float,
    source_provider: str = "polygon",
    sources_seen: Optional[list[str]] = None,
    volume: int = 1000,
    vwap: Optional[float] = None,
    trade_count: Optional[int] = None,
) -> SilverBar:
    """SilverBar fixture (split-adjusted OHLCV — the canonical view)."""
    return SilverBar(
        symbol=symbol,
        timestamp=ts,
        open=close,
        high=close,
        low=close,
        close=close,
        volume=volume,
        vwap=vwap,
        trade_count=trade_count,
        source_provider=source_provider,
        sources_seen=sources_seen or [source_provider],
    )


class _StubReader:
    """Stand-in for SilverOhlcvReader at the backfill layer."""

    def __init__(
        self,
        *,
        response: Optional[SilverBarsResponse] = None,
        raises: Optional[Exception] = None,
    ) -> None:
        self._response = response
        self._raises = raises
        self.last_call: dict = {}

    def get_bars(self, symbol, start, end):
        self.last_call = {"symbol": symbol, "start": start, "end": end}
        if self._raises:
            raise self._raises
        if self._response is not None:
            return self._response
        return SilverBarsResponse(
            symbol=symbol.upper(),
            start=start,
            end=end,
            snapshot_id=None,
            bars=[],
            count=0,
        )


# ─────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────


class TestBackfillResult:
    def test_succeeded_when_no_error(self) -> None:
        r = LakeToChBackfillResult(symbol="AAPL")
        assert r.succeeded is True

    def test_failed_when_error(self) -> None:
        r = LakeToChBackfillResult(symbol="AAPL", error="boom")
        assert r.succeeded is False

    def test_duration_seconds(self) -> None:
        r = LakeToChBackfillResult(
            symbol="AAPL",
            started_at=datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc),
            finished_at=datetime(2024, 1, 1, 0, 0, 10, tzinfo=timezone.utc),
        )
        assert r.duration_seconds == 10.0


# ─────────────────────────────────────────────────────────────────────
# backfill_symbol_window
# ─────────────────────────────────────────────────────────────────────


class TestBackfillHappyPath:
    def test_silver_adj_close_propagates_to_ch(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Silver split-adjusted close maps directly to CH ohlcv_1m.close.
        Worked example: NVDA pre-2024-06-10-split bar has silver
        close=120.88 (the split-adjusted view), and that's exactly
        what CH gets — no provider-direct fetching, no extra math."""
        ts = datetime(2024, 6, 7, 14, 30, tzinfo=timezone.utc)
        bar = _silver_bar("AAPL", ts, close=120.88)
        resp = SilverBarsResponse(
            symbol="AAPL",
            start=datetime(2024, 6, 7, tzinfo=timezone.utc),
            end=datetime(2024, 6, 8, tzinfo=timezone.utc),
            snapshot_id="snap-1",
            bars=[bar],
            count=1,
        )
        reader = _StubReader(response=resp)

        captured: list = []
        monkeypatch.setattr(
            "app.services.ingest.lake_to_ch_backfill.insert_bars_batch",
            lambda rows: captured.extend(rows),
        )

        bf = LakeToChBackfill(reader=reader)
        result = bf.backfill_symbol_window("aapl", resp.start, resp.end)

        assert result.succeeded
        assert result.symbol == "AAPL"
        assert result.bars_read == 1
        assert result.bars_written == 1
        assert result.snapshot_id == "snap-1"
        # Service uppercases before delegating to the reader (silver
        # symbols are canonical uppercase).
        assert reader.last_call["symbol"] == "AAPL"

        assert len(captured) == 1
        row = captured[0]
        assert row["symbol"] == "AAPL"
        assert row["close"] == 120.88
        assert row["source"] == "silver-polygon"

    def test_source_provider_propagates_into_ch_source_tag(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """When silver row came from schwab (precedence flipped), CH
        row tag is 'silver-schwab' — preserves the path's provenance
        even though it ultimately came from silver."""
        ts = datetime(2024, 6, 7, 14, 30, tzinfo=timezone.utc)
        bar = _silver_bar(
            "AAPL", ts, close=100.0, source_provider="schwab",
        )
        resp = SilverBarsResponse(
            symbol="AAPL",
            start=datetime(2024, 6, 7, tzinfo=timezone.utc),
            end=datetime(2024, 6, 8, tzinfo=timezone.utc),
            snapshot_id=None, bars=[bar], count=1,
        )

        captured: list = []
        monkeypatch.setattr(
            "app.services.ingest.lake_to_ch_backfill.insert_bars_batch",
            lambda rows: captured.extend(rows),
        )

        bf = LakeToChBackfill(reader=_StubReader(response=resp))
        bf.backfill_symbol_window("AAPL", resp.start, resp.end)
        assert captured[0]["source"] == "silver-schwab"


class TestBackfillEdgeCases:
    def test_empty_symbol_returns_clean_result(self) -> None:
        bf = LakeToChBackfill(reader=_StubReader())
        result = bf.backfill_symbol_window(
            "",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        assert result.succeeded
        assert result.bars_read == 0
        assert result.bars_written == 0
        assert result.symbol == ""

    def test_cold_start_no_silver_rows_is_not_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Brand-new ad-hoc symbol: silver has zero rows. NOT an error
        — Schwab REST tip-fill (TA-5.3.2) will cover the 48-day reach."""
        captured: list = []
        monkeypatch.setattr(
            "app.services.ingest.lake_to_ch_backfill.insert_bars_batch",
            lambda rows: captured.extend(rows),
        )

        bf = LakeToChBackfill(reader=_StubReader())  # empty response
        result = bf.backfill_symbol_window(
            "NEW_SYMBOL",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        assert result.succeeded
        assert result.bars_read == 0
        assert result.bars_written == 0
        # No CH insert attempted when silver has nothing — saves a
        # round-trip + a CH no-op merge.
        assert captured == []

    def test_reader_failure_captured_as_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """An exception during read is captured as result.error so
        callers can retry without a try/except themselves."""
        monkeypatch.setattr(
            "app.services.ingest.lake_to_ch_backfill.insert_bars_batch",
            lambda rows: None,
        )
        bf = LakeToChBackfill(
            reader=_StubReader(raises=RuntimeError("snapshot expired")),
        )
        result = bf.backfill_symbol_window(
            "AAPL",
            datetime(2024, 1, 1, tzinfo=timezone.utc),
            datetime(2024, 1, 2, tzinfo=timezone.utc),
        )
        assert result.succeeded is False
        assert "RuntimeError" in (result.error or "")

    def test_ch_insert_failure_captured_as_error(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        ts = datetime(2024, 1, 1, 14, 30, tzinfo=timezone.utc)
        bar = _silver_bar("AAPL", ts, close=100.0)
        resp = SilverBarsResponse(
            symbol="AAPL",
            start=datetime(2024, 1, 1, tzinfo=timezone.utc),
            end=datetime(2024, 1, 2, tzinfo=timezone.utc),
            snapshot_id=None, bars=[bar], count=1,
        )

        def _boom(_rows: list) -> None:
            raise RuntimeError("CH connection refused")

        monkeypatch.setattr(
            "app.services.ingest.lake_to_ch_backfill.insert_bars_batch",
            _boom,
        )

        bf = LakeToChBackfill(reader=_StubReader(response=resp))
        result = bf.backfill_symbol_window("AAPL", resp.start, resp.end)
        assert result.succeeded is False
        assert "RuntimeError" in (result.error or "")
        # We still read silver successfully — the failure was on the
        # CH side. Preserve that distinction for the operator.
        assert result.bars_read == 1
        assert result.bars_written == 0


# ─────────────────────────────────────────────────────────────────────
# backfill_symbol (the days-based convenience)
# ─────────────────────────────────────────────────────────────────────


class TestBackfillSymbolDaysWindow:
    def test_default_days_uses_730_lookback(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reader = _StubReader()  # empty response
        monkeypatch.setattr(
            "app.services.ingest.lake_to_ch_backfill.insert_bars_batch",
            lambda rows: None,
        )
        bf = LakeToChBackfill(reader=reader)
        bf.backfill_symbol("AAPL")
        assert reader.last_call["symbol"] == "AAPL"
        # Window should be roughly 730 days wide.
        span = reader.last_call["end"] - reader.last_call["start"]
        assert span.days == DEFAULT_BACKFILL_DAYS

    def test_explicit_days_overrides_default(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        reader = _StubReader()
        monkeypatch.setattr(
            "app.services.ingest.lake_to_ch_backfill.insert_bars_batch",
            lambda rows: None,
        )
        bf = LakeToChBackfill(reader=reader)
        bf.backfill_symbol("AAPL", days=30)
        span = reader.last_call["end"] - reader.last_call["start"]
        assert span.days == 30
