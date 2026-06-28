"""
Economic indicators — free US-government data (BLS + BEA).

Stores raw release values in CH `economic_data` (source of truth for the
Economic page + the AI). Derived figures (YoY, QoQ/MoM change) are computed at
read time, not stored (lean). A genuinely-new release also emits a market-wide
`news_items` row so it hits the feed/digest. Pure functions (parsing, transforms,
headline) are unit-tested without network/CH. See docs/news_alerts_spec.md §14.

Sources:
- BLS (keyless / optional free key): POST series ids → monthly index/level/rate.
- BEA (free key required): GET NIPA tables → quarterly (GDP) or monthly (PCE).
"""
from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)


# ── series catalog ───────────────────────────────────────────────────────
# transform: how the headline figure is derived from the raw series.
#   level     — the value IS the figure (e.g. unemployment %, real-GDP % change).
#   yoy       — year-over-year % from an index (e.g. CPI, PCE price index).
#   mom_delta — period-over-period change in level (e.g. payrolls, thousands).
@dataclass(frozen=True)
class SeriesMeta:
    series_id: str
    name: str
    unit: str
    transform: str
    source: str           # 'bls' | 'bea'
    frequency: str = "M"  # 'M' | 'Q'
    bea_table: str = ""   # BEA NIPA TableName (BEA only)
    bea_line: str = ""    # BEA LineNumber to keep (BEA only)


BLS_SERIES: dict[str, SeriesMeta] = {
    "CUUR0000SA0": SeriesMeta("CUUR0000SA0", "CPI (all items)", "% YoY", "yoy", "bls"),
    "LNS14000000": SeriesMeta("LNS14000000", "Unemployment rate", "%", "level", "bls"),
    "CES0000000001": SeriesMeta("CES0000000001", "Nonfarm payrolls", "k MoM", "mom_delta", "bls"),
}

# BEA NIPA: real GDP % change (T1.1.1 line 1, quarterly) + PCE price index
# (T2.8.4 line 1, monthly — the Fed's preferred inflation gauge, shown YoY).
BEA_SERIES: dict[str, SeriesMeta] = {
    "BEA_GDP": SeriesMeta(
        "BEA_GDP", "Real GDP (QoQ ann.)", "%", "level", "bea",
        frequency="Q", bea_table="T10111", bea_line="1",
    ),
    "BEA_PCEPI": SeriesMeta(
        "BEA_PCEPI", "PCE price index", "% YoY", "yoy", "bea",
        frequency="M", bea_table="T20804", bea_line="1",
    ),
}

ALL_SERIES: dict[str, SeriesMeta] = {**BLS_SERIES, **BEA_SERIES}

_BLS_API = "https://api.bls.gov/publicAPI/v2/timeseries/data/"
_BEA_API = "https://apps.bea.gov/api/data/"
_BLS_URL = "https://data.bls.gov/timeseries/{sid}"
_BEA_URL = "https://www.bea.gov/data"


class BlsError(RuntimeError):
    pass


class BeaError(RuntimeError):
    pass


@dataclass(frozen=True)
class EconPoint:
    series_id: str
    period_key: str      # '2026-05' (monthly) | '2026-Q1' (quarterly)
    period_label: str    # 'May 2026'  | 'Q1 2026'
    value: float


# ── pure period helpers ───────────────────────────────────────────────────
def _monthly_key_label(year: int, month: int, period_name: str = "") -> tuple[str, str]:
    name = period_name or calendar.month_name[month]
    return f"{year}-{month:02d}", f"{name} {year}"


def parse_bea_timeperiod(tp: str) -> Optional[tuple[str, str]]:
    """BEA TimePeriod → (period_key, period_label). '2026Q1' / '2026M05'."""
    tp = (tp or "").strip()
    try:
        if "Q" in tp:
            y, q = tp.split("Q")
            return f"{int(y)}-Q{int(q)}", f"Q{int(q)} {int(y)}"
        if "M" in tp:
            y, m = tp.split("M")
            return _monthly_key_label(int(y), int(m))
    except (ValueError, TypeError):
        return None
    return None


# ── pure parsing ─────────────────────────────────────────────────────────
def parse_bls_response(data: dict) -> dict[str, list[EconPoint]]:
    """BLS API JSON → {series_id: [EconPoint ascending]}. Pure."""
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
                key, label = _monthly_key_label(
                    int(d["year"]), int(period[1:]), str(d.get("periodName", "")),
                )
                pts.append(EconPoint(sid, key, label, float(d["value"])))
            except (KeyError, ValueError, TypeError):
                logger.warning("bls: skipping bad data point for %s: %r", sid, d)
                continue
        pts.sort(key=lambda p: p.period_key)
        out[sid] = pts
    return out


def parse_bea_table(data: dict, *, series_id: str, line: str) -> list[EconPoint]:
    """BEA NIPA GetData JSON → [EconPoint ascending] for one LineNumber. Pure."""
    api = data.get("BEAAPI", {})
    results = api.get("Results", {})
    err = results.get("Error") or api.get("Error")
    if err:
        raise BeaError(f"BEA error: {err}")
    rows = results.get("Data", []) if isinstance(results, dict) else []
    pts: list[EconPoint] = []
    for r in rows:
        if str(r.get("LineNumber", "")) != str(line):
            continue
        parsed = parse_bea_timeperiod(str(r.get("TimePeriod", "")))
        if parsed is None:
            continue
        try:
            value = float(str(r.get("DataValue", "")).replace(",", ""))
        except (ValueError, TypeError):
            continue
        key, label = parsed
        pts.append(EconPoint(series_id, key, label, value))
    pts.sort(key=lambda p: p.period_key)
    return pts


# ── pure transforms / headline ───────────────────────────────────────────
class EconIndicator(BaseModel):
    series_id: str
    name: str
    unit: str
    period_label: str
    value: Optional[float]
    value_label: str
    change: Optional[float]
    raw_value: float


class EconHistoryPoint(BaseModel):
    period: str
    period_label: str
    value: float


def _yoy(points: list[EconPoint], i: int, lag: int) -> Optional[float]:
    if i < lag:
        return None
    prev = points[i - lag].value
    if prev == 0:
        return None
    return (points[i].value / prev - 1.0) * 100.0


def compute_indicator(meta: SeriesMeta, points: list[EconPoint]) -> Optional[EconIndicator]:
    """Latest headline figure + change for a series. Pure. None if no data."""
    if not points:
        return None
    last = points[-1]
    lag = 4 if meta.frequency == "Q" else 12
    value: Optional[float]
    change: Optional[float]
    if meta.transform == "level":
        value = last.value
        change = (last.value - points[-2].value) if len(points) >= 2 else None
        label = f"{value:.1f}%" if "%" in meta.unit else f"{value:.1f}"
    elif meta.transform == "yoy":
        value = _yoy(points, len(points) - 1, lag)
        prev = _yoy(points, len(points) - 2, lag) if len(points) >= 2 else None
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
        period_label=last.period_label, value=value, value_label=label,
        change=change, raw_value=last.value,
    )


def build_release_headline(meta: SeriesMeta, points: list[EconPoint]) -> str:
    """Deterministic feed headline for a new release. Pure."""
    ind = compute_indicator(meta, points)
    if ind is None:
        return f"{meta.name}: new release"
    return f"{meta.name}: {ind.value_label} ({ind.period_label})"


def series_url(series_id: str) -> str:
    meta = ALL_SERIES.get(series_id)
    if meta and meta.source == "bea":
        return _BEA_URL
    return _BLS_URL.format(sid=series_id)


# ── clients ───────────────────────────────────────────────────────────────
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
            "startyear": str(start_year), "endyear": str(end_year),
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


class BeaClient:
    def __init__(self, *, api_key: str, timeout: float = 25.0) -> None:
        self._key = api_key
        self._timeout = timeout

    @classmethod
    def from_settings(cls) -> "BeaClient":
        from app.config import settings
        return cls(api_key=settings.bea_api_key)

    def fetch(self, meta: SeriesMeta, *, start_year: int, end_year: int) -> list[EconPoint]:
        if not self._key:
            raise BeaError("BEA_API_KEY not set")
        import httpx
        years = ",".join(str(y) for y in range(start_year, end_year + 1))
        params = {
            "UserID": self._key, "method": "GetData", "datasetname": "NIPA",
            "TableName": meta.bea_table, "Frequency": meta.frequency,
            "Year": years, "ResultFormat": "json",
        }
        try:
            r = httpx.get(_BEA_API, params=params, timeout=self._timeout)
            r.raise_for_status()
            data = r.json()
        except Exception as e:  # noqa: BLE001 — boundary
            raise BeaError(f"BEA request failed: {e}") from e
        return parse_bea_table(data, series_id=meta.series_id, line=meta.bea_line)


# ── ingest + read service ─────────────────────────────────────────────────
_ECON_COLUMNS = [
    "series_id", "period", "period_label", "value", "source", "ingested_at",
    "version",
]


@dataclass(frozen=True)
class EconIngestResult:
    series: int = 0
    points: int = 0
    releases: int = 0


class EconService:
    def __init__(self, *, bls=None, bea=None, ch_client=None) -> None:
        self._bls = bls
        self._bea = bea
        self._ch = ch_client

    @classmethod
    def from_settings(cls) -> "EconService":
        return cls()

    def _bls_client(self):
        if self._bls is None:
            self._bls = BlsClient.from_settings()
        return self._bls

    def _bea_client(self):
        if self._bea is None:
            self._bea = BeaClient.from_settings()
        return self._bea

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

    def _gather(self, years_back: int) -> dict[str, list[EconPoint]]:
        """Fetch every catalog series, per source. Each source degrades
        independently — a BEA outage (or missing key) never blocks BLS."""
        from app.config import settings

        now = datetime.now(timezone.utc)
        start, end = now.year - years_back, now.year
        by_series: dict[str, list[EconPoint]] = {}

        try:
            by_series.update(self._bls_client().fetch(
                list(BLS_SERIES.keys()), start_year=start, end_year=end,
            ))
        except Exception:  # noqa: BLE001 — boundary
            logger.exception("econ: BLS fetch failed; skipping BLS this run")

        if settings.bea_api_key:
            for sid, meta in BEA_SERIES.items():
                try:
                    by_series[sid] = self._bea_client().fetch(
                        meta, start_year=start, end_year=end,
                    )
                except Exception:  # noqa: BLE001 — boundary
                    logger.exception("econ: BEA fetch failed for %s; skipping", sid)
        else:
            logger.info("econ: BEA_API_KEY not set — skipping GDP/PCE")

        return by_series

    def ingest(self, years_back: int = 3) -> EconIngestResult:
        """Fetch all series, upsert raw points, emit a news item per new release."""
        from app.services.news.service import _NEWS_COLUMNS

        by_series = self._gather(years_back)
        now = datetime.now(timezone.utc)
        version = int(now.timestamp() * 1000)

        econ_rows: list[list] = []
        news_rows: list[list] = []
        releases = 0
        for sid, points in by_series.items():
            if not points:
                continue
            meta = ALL_SERIES.get(sid)
            if meta is None:
                continue
            existing_max = self._existing_max_period(sid)
            for p in points:
                econ_rows.append([sid, p.period_key, p.period_label, p.value, meta.source, now, version])
            latest = points[-1]
            if existing_max is None or latest.period_key > existing_max:
                releases += 1
                headline = build_release_headline(meta, points)
                news_rows.append([
                    f"{meta.source}:{sid}:{latest.period_key}", now, now, meta.source,
                    "econ", "", "", headline, series_url(sid), headline, "",
                    "high", "", 1, version,
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

    def _read_points(self, series_id: str) -> list[EconPoint]:
        r = self._ch_client().query(
            "SELECT period, period_label, value FROM economic_data FINAL "
            "WHERE series_id = {sid:String} ORDER BY period ASC",
            parameters={"sid": series_id},
        )
        return [EconPoint(series_id, row[0], row[1], float(row[2])) for row in r.result_rows]

    def latest(self) -> list[EconIndicator]:
        out: list[EconIndicator] = []
        for sid, meta in ALL_SERIES.items():
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
