"""
Polygon adjustment probe.

Polygon exposes corp-action adjustment behavior in three places we care
about. The probe checks each one:

1. **Polygon REST `/v2/aggs` with `adjusted=false`** — should return
   raw (unadjusted) closes.
2. **Polygon REST `/v2/aggs` with `adjusted=true`** — should return
   split-adjusted closes.
3. **`bronze.polygon_minute` via PyIceberg** — what's actually sitting
   in our lake from the flat-files ingest. This is the production-
   relevant value: bronze is what silver_build will consume. If
   our flat-files import wrote adjusted closes, the silver
   adjustment logic is operating on wrong inputs.

Skipped silently if `POLYGON_API_KEY` is unset (for endpoints 1+2) or
if the bronze table is empty for the probe window (endpoint 3).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

import pandas as pd

from app.config import settings
from app.services.silver.probes import register_probe
from app.services.silver.probes.base import (
    ProbeResult,
    ProbeSpec,
    classify,
    close_to,
)

logger = logging.getLogger(__name__)


@register_probe("polygon")
class PolygonAdjustmentProbe:
    """Probe Polygon's three endpoint adjustment behaviors."""

    provider_name = "polygon"

    async def probe(self, spec: ProbeSpec) -> list[ProbeResult]:
        results: list[ProbeResult] = []
        results += await self._probe_rest(spec, adjusted=False)
        results += await self._probe_rest(spec, adjusted=True)
        results += await self._probe_bronze(spec)
        return results

    # ─────────────────────────────────────────────────────────────────

    async def _probe_rest(self, spec: ProbeSpec, *, adjusted: bool) -> list[ProbeResult]:
        endpoint = f"polygon_rest_adjusted={str(adjusted).lower()}"
        if not settings.polygon_api_key:
            return self._error_results(
                spec, endpoint, "POLYGON_API_KEY not set",
            )

        try:
            # Lazy import — keeps the probe loadable in environments without
            # the polygon SDK pinned.
            from app.providers.polygon_provider import PolygonProvider
            provider = PolygonProvider(api_key=settings.polygon_api_key)
            rest = provider._rest_client()
            aggs = list(
                rest.list_aggs(
                    ticker=spec.symbol,
                    multiplier=1,
                    timespan="day",
                    from_=spec.pre_split_date.isoformat(),
                    to=spec.post_split_date.isoformat(),
                    adjusted=adjusted,
                    limit=50,
                )
            )
        except Exception as e:
            logger.warning("polygon rest probe failed: %s", e, exc_info=False)
            return self._error_results(spec, endpoint, f"{type(e).__name__}: {e}")

        by_date: dict = {}
        for a in aggs:
            ts = datetime.fromtimestamp(a.timestamp / 1000, tz=timezone.utc)
            by_date[ts.date()] = a.close

        return self._make_results(spec, endpoint, by_date)

    async def _probe_bronze(self, spec: ProbeSpec) -> list[ProbeResult]:
        endpoint = "polygon_flatfiles_via_bronze.polygon_minute"
        try:
            from pyiceberg.expressions import (
                And,
                EqualTo,
                GreaterThanOrEqual,
                LessThan,
            )

            from app.services.bronze.tables import ensure_bronze_polygon_minute

            table = ensure_bronze_polygon_minute()
            # The pre-split-date and post-split-date span 4 calendar days;
            # widen by 1 day on each side to be defensive about edge bars.
            start_ts = datetime(
                spec.pre_split_date.year, spec.pre_split_date.month,
                spec.pre_split_date.day, tzinfo=timezone.utc,
            )
            end_ts = datetime(
                spec.post_split_date.year, spec.post_split_date.month,
                spec.post_split_date.day, 23, 59, 59, tzinfo=timezone.utc,
            )
            scan = table.scan(
                row_filter=And(
                    EqualTo("symbol", spec.symbol),
                    GreaterThanOrEqual("timestamp", start_ts),
                    LessThan("timestamp", end_ts + pd.Timedelta(seconds=1).to_pytimedelta()),
                ),
                selected_fields=("symbol", "timestamp", "close"),
            )
            arrow = scan.to_arrow()
        except Exception as e:
            logger.warning("polygon bronze probe failed: %s", e, exc_info=False)
            return self._error_results(spec, endpoint, f"{type(e).__name__}: {e}")

        if arrow.num_rows == 0:
            return self._no_data_results(
                spec, endpoint,
                "bronze.polygon_minute has no rows in the probe window",
            )

        df = arrow.to_pandas()
        df["date"] = pd.to_datetime(df["timestamp"]).dt.tz_convert("America/New_York").dt.date
        # Use the last bar per ET trading day as the day's close.
        daily = df.sort_values("timestamp").groupby("date").last()

        by_date = {
            d: float(daily.loc[d, "close"]) if d in daily.index else None
            for d in (spec.pre_split_date, spec.post_split_date)
        }
        return self._make_results(spec, endpoint, by_date)

    # ─────────────────────────────────────────────────────────────────
    # Result helpers
    # ─────────────────────────────────────────────────────────────────

    def _make_results(
        self,
        spec: ProbeSpec,
        endpoint: str,
        by_date: dict,
    ) -> list[ProbeResult]:
        rs: list[ProbeResult] = []
        for probe_date, expected in (
            (spec.pre_split_date, spec.expected_pre),
            (spec.post_split_date, spec.expected_post),
        ):
            close = by_date.get(probe_date)
            rs.append(
                ProbeResult(
                    provider=self.provider_name,
                    endpoint=endpoint,
                    probe_date=probe_date,
                    returned_close=close,
                    matches_raw=close_to(close, expected.raw),
                    matches_split_adjusted=close_to(close, expected.split_adjusted),
                    classification=classify(close, expected),
                )
            )
        return rs

    def _error_results(
        self,
        spec: ProbeSpec,
        endpoint: str,
        error: str,
    ) -> list[ProbeResult]:
        return [
            ProbeResult(
                provider=self.provider_name,
                endpoint=endpoint,
                probe_date=d,
                returned_close=None,
                matches_raw=False,
                matches_split_adjusted=False,
                classification="error",
                error=error,
            )
            for d in (spec.pre_split_date, spec.post_split_date)
        ]

    def _no_data_results(
        self,
        spec: ProbeSpec,
        endpoint: str,
        note: str,
    ) -> list[ProbeResult]:
        return [
            ProbeResult(
                provider=self.provider_name,
                endpoint=endpoint,
                probe_date=d,
                returned_close=None,
                matches_raw=False,
                matches_split_adjusted=False,
                classification="no_data",
                error=note,
            )
            for d in (spec.pre_split_date, spec.post_split_date)
        ]
