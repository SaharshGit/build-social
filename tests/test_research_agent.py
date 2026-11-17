"""Research tests (market + topic layers, merging, caching). Fake LLMs: no API calls."""

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
import yaml

from buildsocial import research_cache
from buildsocial.agents.research import (
    MarketInput,
    MarketResearchAgent,
    TopicInput,
    TopicResearchAgent,
    merge_reports,
    number_sources,
)
from buildsocial.harness.agent import AgentError
from buildsocial.harness.llm.base import LLMClient, LLMError
from buildsocial.harness.llm.types import Citation, LLMResponse, Usage
from buildsocial.harness.trace import MemoryTracer
from buildsocial.schemas import BrandProfile, Finding, ResearchReport, Source

EXAMPLES = Path(__file__).parent.parent / "examples"
TODAY = date(2026, 11, 17)

SEARCH_TEXT = "Cafes launched monsoon mocktails. Sawari offers mulled wine. Rain expected all week."
CITATIONS = [
    Citation(url="https://redirect/a", title="boldoutline.in", cited_text="Cafes launched monsoon mocktails."),
    Citation(url="https://redirect/b", title="indulgexpress.com", cited_text="Sawari offers mulled wine."),
    Citation(url="https://redirect/a", title="boldoutline.in", cited_text="Rain expected all week."),  # same site again
]


def notes(findings) -> str:
    return json.dumps({
        "summary": "Monsoon drinks are trending.",
        "findings": [{"fact": f, "source_ids": ids} for f, ids in findings],
        "trends": ["Monsoon menus"],
    })


class FakeLLM(LLMClient):
    """Replies in order. Search replies (builtin_tools set) come with citations."""

    def __init__(self, replies, citations=CITATIONS):
        self.replies = list(replies)
        self.citations = citations
        self.calls = []

    async def complete(self, messages, tools=None, output_schema=None, temperature=None, max_tokens=None,
                       builtin_tools=None):
        self.calls.append({"messages": messages, "builtin_tools": builtin_tools, "schema": output_schema})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        is_search = bool(builtin_tools)
        return LLMResponse(
            text=reply,
            citations=self.citations if is_search else [],
            search_queries=["monsoon drinks Bengaluru"] if is_search else [],
            stop_reason="end",
            usage=Usage(input_tokens=10, output_tokens=5, web_searches=1 if is_search else 0),
            model="fake-gemini",
        )


@pytest.fixture
def market_input() -> MarketInput:
    return MarketInput(industry="specialty coffee cafe", market="Bengaluru", today=TODAY)


@pytest.fixture
def topic_input() -> TopicInput:
    return TopicInput(topic="monsoon special", platform="instagram", industry="specialty coffee cafe",
                      market="Bengaluru", today=TODAY)


# --- privacy by construction ----------------------------------------------------


def test_market_input_cannot_hold_brand_or_brief_data():
    # Shared research must never see a brand's private plans: the type has no place for them.
    assert set(MarketInput.model_fields) == {"industry", "market", "today"}


def test_market_search_message_has_no_brand_details():
    brand = BrandProfile.model_validate(yaml.safe_load((EXAMPLES / "brew-house.yaml").read_text()))
    msg = MarketResearchAgent().build_search_message(MarketInput(industry=brand.industry, market=brand.market,
                                                                 today=TODAY))
    assert "Brew House" not in msg and "Indiranagar" not in msg
    assert "specialty coffee cafe" in msg and "Tuesday, 17 November 2026" in msg


# --- the shared search-then-structure core ---------------------------------------


def test_number_sources_merges_same_url():
    sources = number_sources(CITATIONS)
    assert [(s.id, s.title) for s in sources] == [(1, "boldoutline.in"), (2, "indulgexpress.com")]
    assert sources[0].passage == "Cafes launched monsoon mocktails. … Rain expected all week."


async def test_search_then_structure(topic_input):
    llm = FakeLLM([SEARCH_TEXT, notes([("Cafes launched monsoon mocktails", [1]), ("Sawari offers mulled wine", [2])])])
    result = await TopicResearchAgent().run(topic_input, llm)

    # Call 1 searched (no schema); call 2 structured (schema, no search). Gemini can't do both at once.
    assert llm.calls[0]["builtin_tools"] == ["web_search"] and llm.calls[0]["schema"] is None
    assert llm.calls[1]["builtin_tools"] is None and llm.calls[1]["schema"] is not None
    structure_prompt = llm.calls[1]["messages"][1].content
    assert '[1] boldoutline.in: "Cafes launched monsoon mocktails.' in structure_prompt

    report = result.output
    assert [f.source_ids for f in report.findings] == [[1], [2]]
    assert report.source(2).title == "indulgexpress.com"  # attached by code
    assert result.usage == Usage(input_tokens=20, output_tokens=10, web_searches=1)


async def test_made_up_source_number_is_rejected_and_retried(topic_input):
    llm = FakeLLM([SEARCH_TEXT, notes([("Cafes launched mocktails", [7])]), notes([("Cafes launched mocktails", [1])])])
    result = await TopicResearchAgent().run(topic_input, llm)
    assert result.attempts == 2
    assert "source numbers [7] don't exist; valid numbers are [1, 2]" in llm.calls[2]["messages"][-1].content


async def test_finding_without_source_is_rejected(topic_input):
    llm = FakeLLM([SEARCH_TEXT, notes([("Coffee is popular", [])]), notes([])])
    result = await TopicResearchAgent().run(topic_input, llm)
    assert result.output.findings == [] and result.attempts == 2


async def test_failed_structuring_reports_total_cost_including_search(market_input):
    llm = FakeLLM([SEARCH_TEXT] + ["not json"] * 3)
    with pytest.raises(AgentError) as exc:
        await MarketResearchAgent().run(market_input, llm)
    assert exc.value.agent == "market_research"
    assert exc.value.usage == Usage(input_tokens=40, output_tokens=20, web_searches=1)


async def test_empty_search_reply_is_retried(topic_input):
    tracer = MemoryTracer()
    llm = FakeLLM([LLMError("no text", retryable=True), SEARCH_TEXT, notes([("Cafes launched mocktails", [1])])])
    result = await TopicResearchAgent().run(topic_input, llm, tracer)
    assert len(result.output.findings) == 1
    assert [r["type"] for r in tracer.records].count("search_retry") == 1


async def test_quota_error_is_not_retried_again(topic_input):
    llm = FakeLLM([LLMError("limit: 20 requests per day", status_code=429, retryable=True), SEARCH_TEXT])
    with pytest.raises(LLMError, match="20 requests per day"):
        await TopicResearchAgent().run(topic_input, llm)
    assert len(llm.calls) == 1


# --- merging the two layers -------------------------------------------------------


def report(findings, sources, queries=("q",), trends=("t",)) -> ResearchReport:
    return ResearchReport(
        summary="s", trends=list(trends), search_queries=list(queries),
        findings=[Finding(fact=f, source_ids=ids) for f, ids in findings],
        sources=[Source(id=i, title=t, url=u) for i, t, u in sources],
    )


def test_merge_renumbers_sources_and_rewrites_finding_references():
    topic = report([("Topic fact", [1, 2])], [(1, "a.com", "https://a"), (2, "b.com", "https://b")], trends=["x"])
    market = report([("Market fact", [1]), ("Shared-site fact", [2])],
                    [(1, "c.com", "https://c"), (2, "a.com", "https://a")], trends=["x", "y"])
    merged = merge_reports(topic, market)

    assert [(s.id, s.title) for s in merged.sources] == [(1, "a.com"), (2, "b.com"), (3, "c.com")]
    assert [(f.fact, f.source_ids) for f in merged.findings] == [
        ("Topic fact", [1, 2]),       # topic findings first
        ("Market fact", [3]),         # market source 1 -> 3
        ("Shared-site fact", [1]),    # same URL as topic source 1 keeps one number
    ]
    assert merged.trends == ["x", "y"]  # de-duplicated
    assert merged.summary.startswith("Topic: ") and "\nMarket: " in merged.summary


# --- caches --------------------------------------------------------------------------


def test_market_cache_is_shared_by_market_and_week(tmp_path, market_input):
    saved = research_cache.save_market(tmp_path, market_input, report([], []))
    assert saved.name == "specialty-coffee-cafe_bengaluru_2026-W47.json"
    # Another day in the same week: same file. Next week: a miss (research refreshed).
    assert research_cache.load_market(tmp_path, market_input.model_copy(update={"today": date(2026, 11, 19)}))
    assert research_cache.load_market(tmp_path, market_input.model_copy(update={"today": date(2026, 11, 24)})) is None


def test_topic_cache_is_private_per_brand_and_expires(tmp_path, topic_input):
    research_cache.save_topic(tmp_path, "Brew House", topic_input, report([], []))
    assert research_cache.load_topic(tmp_path, "Brew House", topic_input)
    assert research_cache.load_topic(tmp_path, "Other Cafe", topic_input) is None  # never shared
    assert research_cache.load_topic(tmp_path, "Brew House", topic_input.model_copy(update={"topic": "x"})) is None

    # Older than the limit: ignored.
    path = research_cache.topic_path(tmp_path, "Brew House", topic_input)
    entry = research_cache.CachedResearch.model_validate_json(path.read_text())
    old = entry.model_copy(update={"saved_at": datetime.now(timezone.utc) - timedelta(days=8)})
    path.write_text(old.model_dump_json())
    assert research_cache.load_topic(tmp_path, "Brew House", topic_input) is None
