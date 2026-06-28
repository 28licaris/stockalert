"""Unit tests for app.services.news.enrich — pure parsing + injected LLM."""
from __future__ import annotations

from app.services.news.enrich import (
    Enrichment,
    NewsEnricher,
    build_enrich_prompt,
    parse_enrichment,
)

_GOOD = (
    '{"materiality": "high", "sentiment": "positive", '
    '"summary": "Apple announced a buyback.", '
    '"why_it_matters": "Capital return signal."}'
)


def test_parse_valid():
    e = parse_enrichment(_GOOD)
    assert e.materiality == "high"
    assert e.sentiment == "positive"
    assert e.summary == "Apple announced a buyback."
    assert e.why_it_matters == "Capital return signal."


def test_parse_tolerates_code_fence_and_prose():
    fenced = "```json\n" + _GOOD + "\n```"
    assert parse_enrichment(fenced).materiality == "high"
    prosey = "Here you go: " + _GOOD + " (done)"
    assert parse_enrichment(prosey).sentiment == "positive"


def test_parse_garbage_is_unrated():
    assert parse_enrichment("not json at all") == Enrichment.unrated()
    assert parse_enrichment("") == Enrichment.unrated()


def test_parse_clamps_invalid_fields():
    e = parse_enrichment('{"materiality": "huge", "sentiment": "bullish", "summary": "x"}')
    assert e.materiality == "unrated"   # not in low/medium/high
    assert e.sentiment == ""            # not in positive/neutral/negative
    assert e.summary == "x"
    assert e.why_it_matters == ""       # missing key → default


def test_prompt_truncates_long_body():
    body = "A" * 50_000
    prompt = build_enrich_prompt(title="t", form_type="8-K", body_text=body)
    assert "[truncated]" in prompt
    assert len(prompt) < 20_000        # body was capped to ~12k chars


def test_enricher_uses_injected_llm():
    captured = {}

    def fake_llm(system, user):
        captured["system"] = system
        captured["user"] = user
        return _GOOD

    enr = NewsEnricher(llm_complete=fake_llm)
    e = enr.enrich(title="Apple 8-K", form_type="8-K", body_text="…")
    assert e.materiality == "high"
    assert "Apple 8-K" in captured["user"]   # title made it into the prompt
    assert captured["system"]                # system prompt passed
