"""
SEC EDGAR client — the official, free filings feed (API/feed, NOT scraping).

Discovery via the SEC's latest-filings Atom feed
(``/cgi-bin/browse-edgar?action=getcurrent``), company identity via the free
``company_tickers.json`` (CIK↔ticker), and per-filing fetch by the URL the feed
references. EDGAR requires a ``User-Agent`` with a contact email and ≤10 req/s.

The Atom PARSING is pure (`parse_latest_filings`) so it's unit-tested without
network; only `latest_filings` / `fetch_*` touch the wire. See
docs/news_alerts_spec.md.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Sequence

logger = logging.getLogger(__name__)

_ATOM = "{http://www.w3.org/2005/Atom}"
_ACCESSION_RE = re.compile(r"accession-number=([0-9-]+)")
_CIK_IN_TITLE_RE = re.compile(r"\((\d{4,10})\)")


class EdgarError(RuntimeError):
    pass


@dataclass(frozen=True)
class EdgarFiling:
    accession: str          # dedup id, e.g. 0000320193-26-000075
    form_type: str          # 8-K, 4, 10-Q, ...
    company: str
    cik: str                # zero-stripped numeric CIK
    title: str
    url: str                # link to the filing on sec.gov
    published_at: Optional[datetime]


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def parse_latest_filings(atom_xml: str) -> list[EdgarFiling]:
    """Parse an EDGAR getcurrent Atom feed → list[EdgarFiling]. Pure; no I/O.

    Malformed entries are skipped (logged), never raised — one bad entry must
    not drop the whole batch.
    """
    try:
        root = ET.fromstring(atom_xml)
    except ET.ParseError as e:
        raise EdgarError(f"EDGAR feed not parseable: {e}") from e

    out: list[EdgarFiling] = []
    for entry in root.findall(f"{_ATOM}entry"):
        try:
            title = _text(entry.find(f"{_ATOM}title"))
            uid = _text(entry.find(f"{_ATOM}id"))
            acc_m = _ACCESSION_RE.search(uid)
            accession = acc_m.group(1) if acc_m else uid

            cat = entry.find(f"{_ATOM}category")
            form_type = (cat.get("term") if cat is not None else "") or ""
            if not form_type and " - " in title:
                form_type = title.split(" - ", 1)[0].strip()

            # title: "8-K - Apple Inc. (0000320193) (Filer)"
            company = title
            if " - " in title:
                company = title.split(" - ", 1)[1]
            cik_m = _CIK_IN_TITLE_RE.search(company)
            cik = str(int(cik_m.group(1))) if cik_m else ""
            if cik_m:
                company = company[: cik_m.start()].strip()

            link = entry.find(f"{_ATOM}link")
            url = link.get("href") if link is not None else ""

            published_at = None
            upd = _text(entry.find(f"{_ATOM}updated"))
            if upd:
                try:
                    published_at = datetime.fromisoformat(upd)
                except ValueError:
                    pass

            if not accession or not form_type:
                logger.warning("edgar: skipping entry missing accession/form: %r", title)
                continue
            out.append(EdgarFiling(
                accession=accession, form_type=form_type, company=company,
                cik=cik, title=title, url=url, published_at=published_at,
            ))
        except Exception as e:  # noqa: BLE001 — one bad entry must not kill the batch
            logger.warning("edgar: skipped malformed entry (%s)", e)
            continue
    return out


class EdgarClient:
    BASE = "https://www.sec.gov"
    DATA = "https://data.sec.gov"
    _LATEST = "/cgi-bin/browse-edgar"
    _TICKERS = "/files/company_tickers.json"

    def __init__(self, *, user_agent: str, timeout: float = 15.0) -> None:
        if not user_agent or "@" not in user_agent:
            raise EdgarError(
                "EDGAR requires a User-Agent containing a contact email "
                "(set EDGAR_USER_AGENT)"
            )
        self._ua = user_agent
        self._timeout = timeout
        self._ticker_to_cik: Optional[dict[str, str]] = None
        self._cik_to_ticker: Optional[dict[str, str]] = None

    @classmethod
    def from_settings(cls) -> "EdgarClient":
        from app.config import settings
        return cls(user_agent=settings.edgar_user_agent)

    def _get(self, url: str, *, params: Optional[dict] = None) -> str:
        import httpx
        try:
            r = httpx.get(
                url, params=params, headers={"User-Agent": self._ua},
                timeout=self._timeout, follow_redirects=True,
            )
            r.raise_for_status()
            return r.text
        except Exception as e:  # noqa: BLE001 — boundary
            raise EdgarError(f"EDGAR GET failed for {url}: {e}") from e

    def latest_filings(
        self, form_types: Sequence[str] = ("8-K",), count: int = 100,
    ) -> list[EdgarFiling]:
        """Newest filings for each form type (one feed call per type)."""
        seen: dict[str, EdgarFiling] = {}
        for ft in form_types:
            xml = self._get(self.BASE + self._LATEST, params={
                "action": "getcurrent", "type": ft, "count": count,
                "output": "atom",
            })
            for f in parse_latest_filings(xml):
                seen[f.accession] = f
        return list(seen.values())

    def _load_ticker_maps(self) -> None:
        import json
        raw = self._get(self.BASE + self._TICKERS)
        data = json.loads(raw)
        # company_tickers.json: {"0": {"cik_str": 320193, "ticker": "AAPL", ...}}
        t2c, c2t = {}, {}
        for row in data.values():
            cik = str(int(row["cik_str"]))
            tkr = str(row["ticker"]).upper()
            t2c[tkr] = cik
            c2t[cik] = tkr
        self._ticker_to_cik, self._cik_to_ticker = t2c, c2t

    def ticker_for_cik(self, cik: str) -> Optional[str]:
        if self._cik_to_ticker is None:
            self._load_ticker_maps()
        return (self._cik_to_ticker or {}).get(str(int(cik)) if cik else "")

    def cik_for_ticker(self, ticker: str) -> Optional[str]:
        if self._ticker_to_cik is None:
            self._load_ticker_maps()
        return (self._ticker_to_cik or {}).get((ticker or "").upper())

    def fetch_filing_text(self, url: str) -> str:
        """Fetch a specific filing document (for LLM summarization). The body is
        used for analysis only — never re-published; consumers link to `url`."""
        return self._get(url)
