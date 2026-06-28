"""
Macro source — Federal Reserve FOMC statements/minutes via the Fed's official
monetary-policy press RSS (free, no key). Parsed into market-wide news items
(symbol=''), enriched by the same LLM stage as filings.

`parse_fed_rss` is pure (unit-tested without network); only `latest_fomc`
touches the wire. See docs/news_alerts_spec.md §11/§12.
"""
from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Optional

logger = logging.getLogger(__name__)


class FedError(RuntimeError):
    pass


@dataclass(frozen=True)
class MacroItem:
    id: str                 # guid or link — dedup key
    title: str
    url: str
    published_at: Optional[datetime]
    event_type: str = "fomc"


def _text(el) -> str:
    return (el.text or "").strip() if el is not None else ""


def _is_fomc(title: str) -> bool:
    t = title.lower()
    return "fomc" in t or "federal open market committee" in t


def parse_fed_rss(rss_xml: str) -> list[MacroItem]:
    """Parse the Fed monetary press RSS → FOMC MacroItems. Pure; no I/O.

    Non-FOMC items (other monetary releases) are filtered out; malformed
    entries are skipped (logged), never raised.
    """
    try:
        root = ET.fromstring(rss_xml)
    except ET.ParseError as e:
        raise FedError(f"Fed RSS not parseable: {e}") from e

    out: list[MacroItem] = []
    for item in root.iter("item"):
        try:
            title = _text(item.find("title"))
            if not title or not _is_fomc(title):
                continue
            link = _text(item.find("link"))
            guid = _text(item.find("guid")) or link
            if not guid:
                logger.warning("fed rss: skipping FOMC item with no guid/link: %r", title)
                continue
            published_at: Optional[datetime] = None
            pub = _text(item.find("pubDate"))
            if pub:
                try:
                    published_at = parsedate_to_datetime(pub)
                except (TypeError, ValueError):
                    published_at = None
            out.append(MacroItem(
                id=guid, title=title, url=link, published_at=published_at,
            ))
        except Exception as e:  # noqa: BLE001 — one bad entry must not kill the batch
            logger.warning("fed rss: skipped malformed item (%s)", e)
            continue
    return out


class FedClient:
    FEED = "https://www.federalreserve.gov/feeds/press_monetary.xml"

    def __init__(self, *, user_agent: str, timeout: float = 15.0) -> None:
        self._ua = user_agent
        self._timeout = timeout

    @classmethod
    def from_settings(cls) -> "FedClient":
        from app.config import settings
        # A descriptive contact UA is polite; reuse the EDGAR one.
        return cls(user_agent=settings.edgar_user_agent)

    def latest_fomc(self) -> list[MacroItem]:
        import httpx
        try:
            r = httpx.get(
                self.FEED, headers={"User-Agent": self._ua},
                timeout=self._timeout, follow_redirects=True,
            )
            r.raise_for_status()
        except Exception as e:  # noqa: BLE001 — boundary
            raise FedError(f"Fed RSS GET failed: {e}") from e
        return parse_fed_rss(r.text)

    def fetch_text(self, url: str) -> str:
        """Fetch a statement page (for LLM summarization). Analysis only —
        never republished; consumers link to `url`."""
        import httpx
        try:
            r = httpx.get(
                url, headers={"User-Agent": self._ua},
                timeout=self._timeout, follow_redirects=True,
            )
            r.raise_for_status()
            return r.text
        except Exception as e:  # noqa: BLE001 — boundary
            raise FedError(f"Fed GET failed for {url}: {e}") from e
