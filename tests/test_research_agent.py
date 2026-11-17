"""Research agent tests with a fake LLM: no API calls."""

import json
from pathlib import Path

import pytest
import yaml

from buildsocial.agents.research import ResearchAgent, ResearchInput, number_sources
from buildsocial.harness.agent import AgentError
from buildsocial.harness.llm.base import LLMClient, LLMError
from buildsocial.harness.llm.types import Citation, LLMResponse, Usage
from buildsocial.harness.trace import MemoryTracer
from buildsocial.schemas import BrandProfile, PostBrief

EXAMPLE_BRAND = Path(__file__).parent.parent / "examples" / "brew-house.yaml"

SEARCH_TEXT = "Cafes launched monsoon mocktails. Sawari offers mulled wine. Rain expected all week."
CITATIONS = [
    Citation(url="https://redirect/a", title="boldoutline.in", cited_text="Cafes launched monsoon mocktails."),
    Citation(url="https://redirect/b", title="indulgexpress.com", cited_text="Sawari offers mulled wine."),
    Citation(url="https://redirect/a", title="boldoutline.in", cited_text="Rain expected all week."),  # same site again
]


def notes(findings, angles=("Rainy-day ritual", "Local twist")) -> str:
    return json.dumps({
        "summary": "Monsoon drinks are trending in Bengaluru.",
        "findings": [{"fact": f, "source_ids": ids} for f, ids in findings],
        "trends": ["Monsoon menus"],
        "angles": list(angles),
    })


class FakeLLM(LLMClient):
    """Replies in order. A search reply is used when builtin_tools is requested."""

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
def research_input() -> ResearchInput:
    brand = BrandProfile.model_validate(yaml.safe_load(EXAMPLE_BRAND.read_text()))
    return ResearchInput(brand=brand, brief=PostBrief(topic="monsoon special", platform="instagram"))


def test_number_sources_merges_same_url():
    sources = number_sources(CITATIONS)
    assert [(s.id, s.title) for s in sources] == [(1, "boldoutline.in"), (2, "indulgexpress.com")]
    assert sources[0].passage == "Cafes launched monsoon mocktails. … Rain expected all week."


async def test_search_then_structure(research_input):
    llm = FakeLLM([SEARCH_TEXT, notes([("Cafes launched monsoon mocktails", [1]), ("Sawari offers mulled wine", [2])])])
    result = await ResearchAgent().run(research_input, llm)

    # Call 1 searched (no schema); call 2 structured (schema, no search). Gemini can't do both at once.
    assert llm.calls[0]["builtin_tools"] == ["web_search"] and llm.calls[0]["schema"] is None
    assert llm.calls[1]["builtin_tools"] is None and llm.calls[1]["schema"] is not None
    # The structuring prompt received the numbered sources.
    structure_prompt = llm.calls[1]["messages"][1].content
    assert '[1] boldoutline.in: "Cafes launched monsoon mocktails.' in structure_prompt
    assert "[2] indulgexpress.com" in structure_prompt
    assert "- Don't: Don't make health claims about coffee" in structure_prompt  # brand rules reach angles

    report = result.output
    assert [f.source_ids for f in report.findings] == [[1], [2]]
    assert report.source(2).title == "indulgexpress.com"  # attached by code
    assert report.search_queries == ["monsoon drinks Bengaluru"]
    assert result.usage == Usage(input_tokens=20, output_tokens=10, web_searches=1)  # both calls counted


async def test_made_up_source_number_is_rejected_and_retried(research_input):
    llm = FakeLLM([
        SEARCH_TEXT,
        notes([("Cafes launched monsoon mocktails", [7])]),  # source 7 doesn't exist
        notes([("Cafes launched monsoon mocktails", [1])]),
    ])
    result = await ResearchAgent().run(research_input, llm)
    assert result.attempts == 2
    feedback = llm.calls[2]["messages"][-1].content
    assert "source numbers [7] don't exist; valid numbers are [1, 2]" in feedback


async def test_finding_without_source_is_rejected(research_input):
    llm = FakeLLM([SEARCH_TEXT, notes([("Coffee is popular", [])]), notes([])])
    result = await ResearchAgent().run(research_input, llm)
    assert result.output.findings == [] and result.attempts == 2
    assert "has no source numbers" in llm.calls[2]["messages"][-1].content


async def test_no_sources_means_no_findings(research_input):
    llm = FakeLLM([SEARCH_TEXT, notes([("Something", [1])]), notes([])], citations=[])
    result = await ResearchAgent().run(research_input, llm)
    assert result.output.sources == [] and result.output.findings == []
    assert "no sources were found" in llm.calls[1]["messages"][1].content


async def test_failed_structuring_reports_total_cost_including_search(research_input):
    llm = FakeLLM([SEARCH_TEXT] + ["not json"] * 3)
    with pytest.raises(AgentError) as exc:
        await ResearchAgent().run(research_input, llm)
    assert exc.value.agent == "research"
    assert exc.value.usage == Usage(input_tokens=40, output_tokens=20, web_searches=1)  # search + 3 attempts


async def test_trace_records_search_and_structuring(research_input):
    tracer = MemoryTracer()
    llm = FakeLLM([SEARCH_TEXT, notes([("Cafes launched monsoon mocktails", [1])])])
    await ResearchAgent().run(research_input, llm, tracer)

    kinds = [(r["type"], r.get("agent"), r.get("attempt")) for r in tracer.records]
    assert kinds[0] == ("run_start", "research", None)
    assert kinds[1] == ("llm_call", None, "search")
    assert tracer.records[1]["response"]["search_queries"] == ["monsoon drinks Bengaluru"]
    assert kinds[-1][0] == "run_end" and tracer.records[-1]["status"] == "ok"
    assert tracer.records[-1]["output"]["sources"][0]["title"] == "boldoutline.in"


async def test_search_api_error_is_traced_and_raised(research_input):
    tracer = MemoryTracer()
    with pytest.raises(LLMError):
        await ResearchAgent().run(research_input, FakeLLM([LLMError("quota", status_code=429)]), tracer)
    assert tracer.records[-1]["status"] == "error" and "quota" in tracer.records[-1]["error"]


async def test_empty_search_reply_is_retried(research_input):
    empty = LLMError("Gemini returned no text", retryable=True)  # no status_code: a reply problem
    tracer = MemoryTracer()
    llm = FakeLLM([empty, SEARCH_TEXT, notes([("Cafes launched monsoon mocktails", [1])])])
    result = await ResearchAgent().run(research_input, llm, tracer)
    assert len(result.output.findings) == 1
    assert [r["type"] for r in tracer.records].count("search_retry") == 1


async def test_quota_error_is_not_retried_again(research_input):
    quota = LLMError("limit: 20 requests per day", status_code=429, retryable=True)
    llm = FakeLLM([quota, SEARCH_TEXT])
    with pytest.raises(LLMError, match="20 requests per day"):
        await ResearchAgent().run(research_input, llm)
    assert len(llm.calls) == 1  # the HTTP layer already retried; don't burn more quota
