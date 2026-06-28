"""
Economic indicators — free US-government data (BLS now; BEA later).

Stores raw release values in CH `economic_data` (source of truth for the
Economic page + the AI). Derived figures (YoY, MoM change) are computed at read
time, not stored (lean). A genuinely-new release also emits a market-wide
`news_items` row so it hits the feed/digest. Pure functions (parsing, transforms,
headline) are unit-tested without network/CH. See docs/news_alerts_spec.md §14.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ── series catalog ───────────────────────────────────────────────────────
# transform: how the headline figure is derived from the raw series.
#   level     — the value IS the figure (e.g. unemployment %).
#   yoy       — year-over-year % from an index (e.g. CPI).
#   mom_delta — month-over-month change in level (e.g. payrolls, in thousands).
@dataclass(frozen=True)
class SeriesMeta:
    series_id: str
    name: str
    unit: str
    transform: str
    source: str = "bls"


BLS_SERIES: dict[str, SeriesMeta] = {
    "CUUR0000SA0": SeriesMeta("CUUR0000SA0", "CPI (all items)", "% YoY", "yoy"),
    "LNS14000000": SeriesMeta("LNS14000000", "Unemployment rate", "%", "level"),
    "CES0000000001": SeriesMeta("CES0000000001", "Nonfarm payrolls", "k MoM", "mom_delta"),
}

_BLS_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
_SERIES_URL = "https://data.bls.gov/timeseries/{sid}"


class BlsError(RuntimeError):
    pass


@dataclass(frozen=True)
class EconPoint:
    series_id: str
    year: int
    period: str          # 'M05'
    period_name: str     # 'May'
    value: float

    @property
    def month(self) -> int:
        return int(self.period[1:])

    @property
    def ym(self) -> str:
        return f"{self.year}-{self.month:02d}"

    @property
    def label(self) -> str:
        return f"{self.period_name} {self.year}"


# ── pure parsing ─────────────────────────────────────────────────────────
def parse_bls_response(data: dict) -> dict[str, list[EconPoint]]:
    """BLS API JSON → {series_id: [EconPoint ascending by period]}. Pure.

    Skips annual averages (M13) and unparseable values; raises only on a
    non-success status (so a transport-level problem is loud, a single bad
    row is not).
    """
    status = str(data.get("status", ""))
    if status and status != "REQUEST_SUCCEEDED":
        raise BlsError(f"BLS status {status}: {data.get('message')}")

    out: dict[str, list[EconPoint]] = {}
    for series in data.get("Results", {}).get("series", []):
        sid = series.get("seriesID", "")
        pts: list[EconPoint] = []
        for d in series.get("data", []):
            period = str(d.get("period", ""))
            if not period.startswith("M") or period == "M13":
                continue
            try:
                pts.append(EconPoint(
                    series_id=sid, year=int(d["year"]), period=period,
                    period_name=str(d.get("periodName", "")),
                    value=float(d["value"]),
                ))
            except (KeyError, ValueError, TypeError):
                logger.warning("bls: skipping bad data point for %s: %r", sid, d)
                continue
        pts.sort(key=lambda p: (p.year, p.month))
        out[sid] = pts
    return out


# ── pure transforms / headline ───────────────────────────────────────────
class EconIndicator(BaseModel):
    series_id: str
    name: str
    unit: str
    period_label: str
    value: Optional[float]        # the headline figure (transformed)
    value_label: str             # formatted, e.g. '3.2%' or '+180k'
    change: Optional[float]      # vs the prior period's figure
    raw_value: float


class EconHistoryPoint(BaseModel):
    period: str
    period_label: str
    value: float


def _yoy(points: list[EconPoint], i: int) -> Optional[float]:
    if i < 12:
        return None
    prev = points[i - 12].value
    if prev == 0:
        return None
    return (points[i].value / prev - 1.0) * 100.0


def compute_indicator(meta: SeriesMeta, points: list[EconPoint]) -> Optional[EconIndicator]:
    """Latest headline figure + change for a series. Pure. None if no data."""
    if not points:
        return None
    last = points[-1]
    value: Optional[float]
    change: Optional[float]
    if meta.transform == "level":
        value = last.value
        change = (last.value - points[-2].value) if len(points) >= 2 else None
        label = f"{value:.1f}{meta.unit}"
    elif meta.transform == "yoy":
        value = _yoy(points, len(points) - 1)
        prev = _yoy(points, len(points) - 2) if len(points) >= 2 else None
        change = (value - prev) if (value is not None and prev is not None) else None
        label = f"{value:.1f}%" if value is not None else "—"
    elif meta.transform == "mom_delta":
        value = (last.value - points[-2].value) if len(points) >= 2 else None
        prev = (points[-2].value - points[-3].value) if len(points) >= 3 else None
        change = (value - prev) if (value is not None and prev is not None) else None
        label = f"{value:+.0f}k" if value is not None else "—"
    else:
        value, change, label = last.value, None, f"{last.value}"
    return EconIndicator(
        series_id=meta.series_id, name=meta.name, unit=meta.unit,
        period_label=last.label, value=value, value_label=label,
        change=change, raw_value=last.value,
    )


def build_release_headline(meta: SeriesMeta, points: list[EconPoint]) -> str:
    """Deterministic feed headline for a new release. Pure."""
    ind = compute_indicator(meta, points)
    if ind is None:
        return f"{meta.name}: new release"
    return f"{meta.name}: {ind.value_label} ({ind.period_label})"


# ── client ───────────────────────────────────────────────────────────────
class BlsClient:
    def __init__(self, *, api_key: str = "", timeout: float = 20.0) -> None:
        self._key = api_key
        self._timeout = timeout

    @classmethod
    def from_settings(cls) -> "BlsClient":
        from app.config import settings
        return cls(api_key=settings.bls_api_key)

    def fetch(self, series_ids: list[str], *, start_year: int, end_year: int) -> dict[str, list[EconPoint]]:
        import httpx
        payload: dict = {
            "seriesid": list(series_ids),
            "startyear": str(start_year),
            "endyear": str(end_year),
        }
        if self._key:
            payload["registrationkey"] = self._key
        try:
            r = httpx.post(_BLS_API, json=payload, timeout=self._timeout)
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001 — boundary
            raise BlsError(f"BLS request failed: {e}") from e
        return parse_bls_response(data)


def series_url(series_id: str) -> str:
    return _SERIES_URL.format(sid=series_id)


# ── ingest + read service ─────────────────────────────────────────────────
import calendar  # noqa: E402

_ECON_COLUMNS = [
    "series_id", "period", "period_label", "value", "source", "ingested_at",
    "version",
]


@dataclass(frozen=True)
class EconIngestResult:
    series: int = 0
    points: int = 0      # rows upserted into economic_data
    releases: int = 0    # genuinely-new releases that emitted a news item


def _point_from_row(series_id: str, period: str, value: float) -> EconPoint:
    """Reconstruct an EconPoint from a stored ('YYYY-MM', value) row."""
    year_s, month_s = period.split("-")
    month = int(month_s)
    return EconPoint(
        series_id=series_id, year=int(year_s), period=f"M{month:02d}",
        period_name=calendar.month_name[month], value=value,
    )


class EconService:
    def __init__(self, *, bls=None, ch_client=None) -> None:
        self._bls = bls
        self._ch = ch_client

    @classmethod
    def from_settings(cls) -> "EconService":
        return cls()

    def _bls_client(self):
        if self._bls is None:
            self._bls = BlsClient.from_settings()
        return self._bls

    def _ch_client(self):
        if self._ch is None:
            from app.db.client import get_client
            self._ch = get_client()
        return self._ch

    def _existing_max_period(self, series_id: str) -> Optional[str]:
        r = self._ch_client().query(
            "SELECT max(period) FROM economic_data WHERE series_id = {sid:String}",
            parameters={"sid": series_id},
        )
        val = r.result_rows[0][0] if r.result_rows else None
        return val or None

    def _read_points(self, series_id: str) -> list[EconPoint]:
        r = self._ch_client().query(
            "SELECT period, value FROM economic_data FINAL "
            "WHERE series_id = {sid:String} ORDER BY period ASC",
            parameters={"sid": series_id},
        )
        return [_point_from_row(series_id, row[0], float(row[1])) for row in r.result_rows]

    def ingest(self, series_ids: Optional[list[str]] = None, years_back: int = 2) -> EconIngestResult:
        """Fetch BLS series, upsert raw points, and emit a news item per
        genuinely-new release (deterministic headline, enriched=1)."""
        from app.services.news.service import _NEWS_COLUMNS

        ids = list(series_ids or BLS_SERIES.keys())
        now = datetime.now(timezone.utc)
        end_year = now.year
        by_series = self._bls_client().fetch(
            ids, start_year=end_year - years_back, end_year=end_year
        )
        version = int(now.timestamp() * 1000)

        econ_rows: list[list] = []
        news_rows: list[list] = []
        releases = 0
        for sid, points in by_series.items():
            if not points:
                continue
            meta = BLS_SERIES.get(sid)
            src = meta.source if meta else "bls"
            existing_max = self._existing_max_period(sid)
            for p in points:
                econ_rows.append([sid, p.ym, p.label, p.value, src, now, version])
            latest = points[-1]
            if meta and (existing_max is None or latest.ym > existing_max):
                releases += 1
                headline = build_release_headline(meta, points)
                news_rows.append([
                    f"bls:{sid}:{latest.ym}", now, now, src, "econ", "", "",
                    headline, series_url(sid), headline, "", "high", "", 1, version,
                ])

        if econ_rows:
            self._ch_client().insert("economic_data", econ_rows, column_names=_ECON_COLUMNS)
        if news_rows:
            self._ch_client().insert("news_items", news_rows, column_names=_NEWS_COLUMNS)

        result = EconIngestResult(
            series=sum(1 for p in by_series.values() if p),
            points=len(econ_rows), releases=releases,
        )
        logger.info(
            "econ ingest: series=%d points=%d releases=%d",
            result.series, result.points, result.releases,
        )
        return result

    def latest(self) -> list[EconIndicator]:
        out: list[EconIndicator] = []
        for sid, meta in BLS_SERIES.items():
            ind = compute_indicator(meta, self._read_points(sid))
            if ind is not None:
                out.append(ind)
        return out

    def history(self, series_id: str, limit: int = 60) -> list[EconHistoryPoint]:
        n = max(1, min(int(limit), 600))
        r = self._ch_client().query(
            "SELECT period, period_label, value FROM economic_data FINAL "
            "WHERE series_id = {sid:String} ORDER BY period DESC LIMIT " + str(n),
            parameters={"sid": series_id},
        )
        return [
            EconHistoryPoint(period=row[0], period_label=row[1], value=float(row[2]))
            for row in r.result_rows
        ]
