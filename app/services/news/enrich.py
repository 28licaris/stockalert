"""
LLM enrichment — turn a raw filing into {materiality, sentiment, summary,
why_it_matters}. Cost-capped: only watchlist-relevant filings reach here, the
body is truncated before sending, and output tokens are capped.

`parse_enrichment` / `build_enrich_prompt` are pure (unit-tested without the
SDK). `NewsEnricher` takes an injectable `llm_complete` for tests; the real
path lazy-loads the Anthropic SDK (same ANTHROPIC_API_KEY as the assistant).
We send the body for ANALYSIS only — it is never stored/republished; consumers
get our summary + the source link. See docs/news_alerts_spec.md.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Callable, Optional

logger = logging.getLogger(__name__)

_VALID_MATERIALITY = {"low", "medium", "high"}
_VALID_SENTIMENT = {"positive", "neutral", "negative"}

# Cost guards.
_MAX_BODY_CHARS = 12_000     # don't ship a whole 10-K to the model
_MAX_SUMMARY_CHARS = 400
_MAX_WHY_CHARS = 400
_DEFAULT_MAX_TOKENS = 600

_SYSTEM = (
    "You are an equities analyst. You read one SEC filing (or excerpt) and "
    "produce a terse, factual read for a retail trader. Never speculate beyond "
    "the document. Respond with ONLY a JSON object, no prose, no code fences."
)


@dataclass(frozen=True)
class Enrichment:
    materiality: str        # low | medium | high  (unrated if the model failed)
    sentiment: str          # positive | neutral | negative  ('' if unknown)
    summary: str            # one factual sentence
    why_it_matters: str     # one sentence on trading relevance

    @classmethod
    def unrated(cls) -> "Enrichment":
        return cls(materiality="unrated", sentiment="", summary="", why_it_matters="")


def build_enrich_prompt(*, title: str, form_type: str, body_text: str) -> str:
    """Pure — assemble the user prompt, truncating the body to the char cap."""
    body = (body_text or "").strip()
    if len(body) > _MAX_BODY_CHARS:
        body = body[:_MAX_BODY_CHARS] + "\n…[truncated]"
    return (
        f"Filing type: {form_type}\n"
        f"Title: {title}\n\n"
        f"Document (may be truncated):\n{body}\n\n"
        "Return JSON with exactly these keys:\n"
        '{"materiality": "low|medium|high", '
        '"sentiment": "positive|neutral|negative", '
        '"summary": "one factual sentence on what was filed", '
        '"why_it_matters": "one sentence on why a trader should care"}'
    )


def parse_enrichment(raw: str) -> Enrichment:
    """Pure — parse the model's JSON reply into a validated Enrichment.

    Tolerates code fences / surrounding prose by extracting the outermost
    JSON object. Invalid/missing fields are clamped to safe defaults; an
    unparseable reply yields `Enrichment.unrated()` (logged, never raised) so
    one bad enrichment never drops the batch.
    """
    text = (raw or "").strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        logger.warning("news enrich: no JSON object in reply (%r…)", text[:80])
        return Enrichment.unrated()
    try:
        obj = json.loads(text[start : end + 1])
    except (ValueError, TypeError) as e:
        logger.warning("news enrich: JSON parse failed (%s)", e)
        return Enrichment.unrated()

    materiality = str(obj.get("materiality", "")).strip().lower()
    if materiality not in _VALID_MATERIALITY:
        materiality = "unrated"
    sentiment = str(obj.get("sentiment", "")).strip().lower()
    if sentiment not in _VALID_SENTIMENT:
        sentiment = ""
    summary = str(obj.get("summary", "")).strip()[:_MAX_SUMMARY_CHARS]
    why = str(obj.get("why_it_matters", "")).strip()[:_MAX_WHY_CHARS]
    return Enrichment(
        materiality=materiality, sentiment=sentiment,
        summary=summary, why_it_matters=why,
    )


class NewsEnricher:
    def __init__(
        self,
        *,
        llm_complete: Optional[Callable[[str, str], str]] = None,
        model: Optional[str] = None,
        max_tokens: int = _DEFAULT_MAX_TOKENS,
    ) -> None:
        # llm_complete(system, user) -> raw text. Injected in tests.
        self._llm = llm_complete
        self._model = model
        self._max_tokens = max_tokens

    @classmethod
    def from_settings(cls) -> "NewsEnricher":
        from app.config import settings
        return cls(model=settings.news_enrich_model)

    def _complete(self, system: str, user: str) -> str:
        if self._llm is not None:
            return self._llm(system, user)
        from anthropic import Anthropic  # lazy — keeps the package import cheap
        client = Anthropic()  # reads ANTHROPIC_API_KEY from the env
        resp = client.messages.create(
            model=self._model or "claude-haiku-4-5-20251001",
            max_tokens=self._max_tokens,
            system=system,
            messages=[{"role": "user", "content": user}],
        )
        parts = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "".join(parts)

    def enrich(self, *, title: str, form_type: str, body_text: str) -> Enrichment:
        prompt = build_enrich_prompt(title=title, form_type=form_type, body_text=body_text)
        raw = self._complete(_SYSTEM, prompt)
        return parse_enrichment(raw)
