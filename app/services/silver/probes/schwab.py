"""
Schwab adjustment probe.

Schwab's `/marketdata/v1/pricehistory` does NOT document its
adjustment behavior. Their docs (see `docs/schwab-api/market_data_api.md`)
have zero references to "adjust" or "split". This probe is the only
way to know what their API actually returns.

Schwab daily history reaches multi-year (their 1-min history is the
~48 day window — too short to reach the canonical 2020 probe). The
probe uses daily bars.

Single endpoint: `schwab_pricehistory_daily`. If Schwab CHART_EQUITY
stream is observed to disagree later, add a second endpoint probe
here for it (we can't probe historical stream behavior; we'd have to
catch it live around a future split).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

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


@register_probe("schwab")
class SchwabAdjustmentProbe:
    """Probe Schwab's pricehistory adjustment behavior."""

    provider_name = "schwab"

    async def probe(self, spec: ProbeSpec) -> list[ProbeResult]:
        endpoint = "schwab_pricehistory_daily"

        if not (
            settings.schwab_client_id
            and settings.schwab_client_secret
            and settings.get_schwab_refresh_token()
        ):
            return self._error_results(
                spec, endpoint, "Schwab credentials not set",
            )

        try:
            from app.providers.schwab_provider import SchwabProvider

            provider = SchwabProvider(
                settings.schwab_client_id,
                settings.schwab_client_secret,
                refresh_token=settings.get_schwab_refresh_token(),
            )
            # Widen window by 1 day on each side to be defensive.
            start = datetime(
                spec.pre_split_date.year, spec.pre_split_date.month,
                spec.pre_split_date.day, tzinfo=timezone.utc,
            )
            end = datetime(
                spec.post_split_date.year, spec.post_split_date.month,
                spec.post_split_date.day, 23, 59, 59, tzinfo=timezone.utc,
            )
            df = await provider.historical_df(
                spec.symbol, start, end, timeframe="1d",
            )
        except Exception as e:
            logger.warning("schwab probe failed: %s", e, exc_info=False)
            return self._error_results(spec, endpoint, f"{type(e).__name__}: {e}")

        if df.empty:
            return self._no_data_results(
                spec, endpoint, "Schwab returned no daily bars in window",
            )

        df_local = df.copy()
        df_local["date"] = (
            pd.to_datetime(df_local.index).tz_convert("America/New_York").date
        )

        rs: list[ProbeResult] = []
        for probe_date, expected in (
            (spec.pre_split_date, spec.expected_pre),
            (spec.post_split_date, spec.expected_post),
        ):
            match = df_local[df_local["date"] == probe_date]
            close = float(match["close"].iloc[0]) if not match.empty else None
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

    # ─────────────────────────────────────────────────────────────────

    def _error_results(
        self, spec: ProbeSpec, endpoint: str, error: str,
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
        self, spec: ProbeSpec, endpoint: str, note: str,
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
