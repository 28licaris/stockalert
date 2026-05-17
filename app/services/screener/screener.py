"""
Screener — scan a universe with a declarative `ScreenerSpec` and
return ranked candidates.

This is the "fast filter" stage of the canonical swing-trade
pipeline (per `docs/trading_subsystem_design.md` Phase TA-4.3):

    universe (1000s of symbols) -> screener -> 10-30 candidates -> strategy

Bar source resolution mirrors `Backtester` / `IndicatorReader`:
  - `interval='1m'`  -> `BronzeReader` (snapshot pinned)
  - everything else  -> `BarReader` (CH live tier)

Pure-function design: takes a spec + a clock, returns a
`ScreenerResult`. No side effects beyond logging. Safe to call from
HTTP routes, MCP tools, and (future) automated discovery jobs.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

import pandas as pd

from app.services.readers.schemas import BronzeBar
from app.services.screener.rules import RuleEval, evaluate
from app.services.screener.schemas import (
    Candidate,
    CandidateMetric,
    ScreenerResult,
    ScreenerSpec,
)

logger = logging.getLogger(__name__)


class Screener:
    """
    Scan a universe with a `ScreenerSpec`.

    Construct via `from_settings()` for the common path; pass
    explicit dependencies for tests.
    """

    def __init__(
        self,
        *,
        bronze_reader=None,
        bar_reader=None,
        watchlist_service=None,
    ) -> None:
        self._bronze_reader = bronze_reader
        self._bar_reader = bar_reader
        self._watchlist_service = watchlist_service

    @classmethod
    def from_settings(cls) -> "Screener":
        return cls()

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    def scan(self, spec: ScreenerSpec, *, now: Optional[datetime] = None) -> ScreenerResult:
        """
        Execute the scan. Returns ranked `Candidate`s + diagnostics.
        Per-symbol failures (missing data, indicator errors) land in
        `errors`; the scan completes regardless.
        """
        as_of = now or datetime.now(timezone.utc)
        universe = self._resolve_universe(spec)
        snapshot_id = self._capture_snapshot(spec)

        passed: list[Candidate] = []
        rejected = 0
        errors: list[dict[str, str]] = []

        for symbol in universe:
            try:
                df = self._fetch_bars_df(symbol, spec, as_of)
            except Exception as exc:  # noqa: BLE001 — per-symbol boundary
                logger.warning("Screener: fetch failed for %s: %s", symbol, exc)
                errors.append({"symbol": symbol, "error": f"fetch: {type(exc).__name__}: {exc}"})
                continue

            if df is None or df.empty:
                rejected += 1
                continue

            try:
                evals = [evaluate(rule, df) for rule in spec.rules]
            except ValueError:
                # Bad rule kind / missing param — author error. Re-raise to
                # surface clearly; the same bad spec would fail on every
                # symbol so there's no point continuing.
                raise
            except Exception as exc:  # noqa: BLE001 — per-symbol indicator failure
                logger.warning("Screener: rule eval failed for %s: %s", symbol, exc)
                errors.append({"symbol": symbol, "error": f"eval: {type(exc).__name__}: {exc}"})
                continue

            if not all(e.passed for e in evals):
                rejected += 1
                continue

            candidate = self._build_candidate(symbol, df, evals, spec)
            passed.append(candidate)

        passed = self._rank(passed, spec)
        if len(passed) > spec.limit:
            passed = passed[: spec.limit]

        return ScreenerResult(
            interval=spec.interval,
            universe_size=len(universe),
            n_passed=len(passed),
            candidates=passed,
            rank_by=spec.rank_by,
            as_of=as_of,
            snapshot_id=snapshot_id,
            rejected_count=rejected + len(errors),
            errors=errors,
        )

    # ─────────────────────────────────────────────────────────────────
    # Universe resolution
    # ─────────────────────────────────────────────────────────────────

    def _resolve_universe(self, spec: ScreenerSpec) -> list[str]:
        """
        Merge `spec.universe` + symbols from `spec.watchlist_name`.
        Deduped + uppercased; order: explicit list first, then watchlist
        members.
        """
        seen: set[str] = set()
        out: list[str] = []
        for s in spec.universe:
            sym = (s or "").strip().upper()
            if sym and sym not in seen:
                seen.add(sym)
                out.append(sym)
        if spec.watchlist_name:
            wl = self._get_watchlist_service()
            try:
                members = wl.list_members(spec.watchlist_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "Screener: watchlist %r lookup failed: %s",
                    spec.watchlist_name, exc,
                )
                members = []
            for s in members:
                sym = (s or "").strip().upper()
                if sym and sym not in seen:
                    seen.add(sym)
                    out.append(sym)
        return out

    def _get_watchlist_service(self):
        if self._watchlist_service is not None:
            return self._watchlist_service
        # Lazy import keeps the module light at top-level and lets the
        # screener be unit-tested with an injected stub.
        from app.services.live.watchlist_service import watchlist_service
        return watchlist_service

    # ─────────────────────────────────────────────────────────────────
    # Bar fetching
    # ─────────────────────────────────────────────────────────────────

    def _fetch_bars_df(
        self,
        symbol: str,
        spec: ScreenerSpec,
        as_of: datetime,
    ) -> Optional[pd.DataFrame]:
        """
        Pull the latest `spec.lookback_bars` bars for `symbol` at the
        configured interval. Returns a DataFrame indexed by timestamp
        with OHLCV columns, or None on empty / unknown symbol.
        """
        if spec.interval == "1m":
            bars = self._fetch_bronze(symbol, spec, as_of)
        else:
            bars = self._fetch_ch(symbol, spec, as_of)
        if not bars:
            return None
        return _bars_to_df(bars)

    def _fetch_bronze(self, symbol: str, spec: ScreenerSpec, as_of: datetime):
        from app.services.readers.bronze_reader import BronzeReader

        reader = self._bronze_reader or BronzeReader.from_settings()
        # Approximate: 1m, lookback_bars * 1.5 minutes back from now (buffer
        # for non-trading minutes).
        start = as_of - timedelta(minutes=int(spec.lookback_bars * 1.5))
        return list(reader.get_bars(
            symbol, start, as_of, provider=spec.provider, limit=spec.lookback_bars,
        ))

    def _fetch_ch(self, symbol: str, spec: ScreenerSpec, as_of: datetime):
        from app.services.readers.bar_reader import BarReader

        reader = self._bar_reader or BarReader.from_settings()
        # Approximate: window enough that `lookback_bars` bars exist
        # for this interval. For 1d, lookback_bars * 1.5 calendar days
        # covers weekends + holidays. For 5m we use lookback_bars * 5
        # minutes * 2 for non-trading hours.
        if spec.interval == "1d":
            start = as_of - timedelta(days=int(spec.lookback_bars * 1.5))
        else:
            # Crude back-of-envelope for intraday intervals.
            minutes_per_bar = {
                "1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "4h": 240,
            }.get(spec.interval, 60)
            start = as_of - timedelta(
                minutes=int(spec.lookback_bars * minutes_per_bar * 2),
            )
        return list(reader.get_bars_in_range(
            symbol, start, as_of, interval=spec.interval,
            limit=spec.lookback_bars,
        ))

    def _capture_snapshot(self, spec: ScreenerSpec) -> Optional[str]:
        """Pin Iceberg snapshot when reading from bronze ('1m')."""
        if spec.interval != "1m":
            return None
        try:
            from app.config import settings
            from app.services.iceberg_catalog import get_catalog

            table_id = f"{settings.iceberg_glue_database}.{spec.provider}_minute"
            snap = get_catalog().load_table(table_id).current_snapshot()
            return str(snap.snapshot_id) if snap else None
        except Exception as exc:  # noqa: BLE001
            logger.warning("Screener: snapshot capture failed: %s", exc)
            return None

    # ─────────────────────────────────────────────────────────────────
    # Candidate construction + ranking
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _build_candidate(
        symbol: str, df: pd.DataFrame, evals: list[RuleEval], spec,
    ) -> Candidate:
        """Build a Candidate with per-rule metrics + a sort score."""
        metrics = [
            CandidateMetric(name=ev.metric_name, value=ev.metric_value)
            for ev in evals
        ]
        score = _compute_score(symbol, df, spec.rank_by, evals)
        return Candidate(symbol=symbol, score=score, metrics=metrics)

    @staticmethod
    def _rank(candidates: list[Candidate], spec) -> list[Candidate]:
        """Sort by score per `spec.rank_by` semantics."""
        if spec.rank_by == "none":
            return candidates
        # `rsi` (low-to-high oversold) sorts ASCENDING; everything else
        # sorts DESCENDING (higher score = better candidate).
        ascending = spec.rank_by == "rsi"
        return sorted(
            candidates,
            key=lambda c: c.score,
            reverse=not ascending,
        )


def _compute_score(
    symbol: str, df: pd.DataFrame, rank_by: str, evals: list[RuleEval],
) -> float:
    """
    Score per the `rank_by` policy:
      - 'volume'    : latest volume
      - 'atr_pct'   : ATR(14)/close pulled from the latest bar
      - 'rsi'       : RSI value if one of the rules produced one; else 50
      - 'rsi_desc'  : same as 'rsi' (sort direction flips in _rank)
      - 'none'      : 0.0 (untouched order)
    """
    if rank_by == "none":
        return 0.0
    if rank_by == "volume":
        if "volume" not in df.columns or len(df) == 0:
            return 0.0
        v = df["volume"].iloc[-1]
        try:
            return float(v)
        except (TypeError, ValueError):
            return 0.0
    if rank_by == "atr_pct":
        # Read directly from the bars rather than calling get_indicator
        # again — the rules already computed it if it was needed.
        try:
            from app.indicators.registry import get_indicator
            atr = get_indicator("atr", period=14).compute(
                df["close"], df["high"], df["low"],
            )
            close = float(df["close"].iloc[-1])
            atr_val = float(atr.iloc[-1])
            if close > 0:
                return atr_val / close
        except Exception:  # noqa: BLE001 — score=0 on indicator failure
            pass
        return 0.0
    if rank_by in ("rsi", "rsi_desc"):
        # Prefer an RSI value from one of the rules if present.
        for ev in evals:
            if ev.metric_name.startswith("rsi_") and ev.metric_value is not None:
                return float(ev.metric_value)
        # Else compute a default RSI(14).
        try:
            from app.indicators.registry import get_indicator
            rsi = get_indicator("rsi", period=14).compute(df["close"])
            return float(rsi.iloc[-1])
        except Exception:  # noqa: BLE001
            return 50.0
    return 0.0


def _bars_to_df(bars: Iterable) -> pd.DataFrame:
    """OHLCV DataFrame indexed by timestamp. One row per bar."""
    rows = [
        {
            "timestamp": b.timestamp,
            "open": b.open, "high": b.high, "low": b.low,
            "close": b.close, "volume": b.volume,
        }
        for b in bars
    ]
    df = pd.DataFrame(rows)
    if not df.empty:
        df = df.set_index("timestamp")
    return df
