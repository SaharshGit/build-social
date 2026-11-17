"""Strategy agent, Content-with-plan, and pipeline tests. Fake LLMs; the research is the
real report saved in examples/research/. No API calls."""

import json
from pathlib import Path

import pytest
import yaml

from buildsocial.agents.content import ContentAgent, ContentInput
from buildsocial.agents.strategy import StrategyAgent, StrategyInput
from buildsocial.harness.llm.base import LLMClient, LLMError
from buildsocial.harness.llm.registry import Pricing, TokenPrices
from buildsocial.harness.llm.types import LLMResponse, Usage
from buildsocial.harness.trace import MemoryTracer
from buildsocial.pipeline import run_pipeline
from buildsocial.schemas import BrandProfile, ContentPlan, PostBrief, ResearchReport

ROOT = Path(__file__).parent.parent
BRAND = BrandProfile.model_validate(yaml.safe_load((ROOT / "examples" / "brew-house.yaml").read_text()))
RESEARCH = ResearchReport.model_validate_json(
    (ROOT / "examples" / "research" / "brew-house-masala-chai.json").read_text(encoding="utf-8")
)
BRIEF = PostBrief(topic="monsoon special: new Masala Chai Cold Brew", platform="instagram", goal="sales")


def plan_json(finding_ids=(3, 8), concerns=("The monsoon has mostly ended by November.",)) -> str:
    return json.dumps({
        "angle": "A local twist for Bengaluru's coffee lovers",
        "key_message": "Masala chai meets cold brew at Brew House.",
        "finding_ids": list(finding_ids),
        "cta_direction": "Visit the Indiranagar cafe this week",
        "brief_concerns": list(concerns),
        "rationale": "Ties the drink to the city's coffee culture.",
    })


DRAFT_JSON = json.dumps({
    "platform": "instagram",
    "hook": "Chai ya coffee? Dono, ek glass mein.",
    "body": "Bengaluru loves its coffee. Ab try karo our new Masala Chai Cold Brew.",
    "call_to_action": "Come by Brew House, Indiranagar this week.",
    "hashtags": ["#BrewHouse", "#BengaluruCafes", "#ColdBrew"],
    "image_idea": "The drink on the cafe counter.",
    "rationale": "Plays on the chai vs coffee debate.",
})


class FakeLLM(LLMClient):
    def __init__(self, replies, model="fake"):
        self.replies = list(replies)
        self.model = model
        self.calls = []

    async def complete(self, messages, tools=None, output_schema=None, temperature=None, max_tokens=None,
                       builtin_tools=None):
        self.calls.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(text=reply, stop_reason="end", usage=Usage(input_tokens=1000, output_tokens=100),
                           model=self.model)


# --- Strategy agent ----------------------------------------------------------


def test_strategy_message_includes_todays_date():
    from datetime import date

    msg = StrategyAgent().build_user_message(
        StrategyInput(brand=BRAND, brief=BRIEF, research=RESEARCH, today=date(2026, 11, 17))
    )
    assert msg.startswith("Today's date: Tuesday, 17 November 2026")


def test_strategy_message_numbers_findings_with_sources_and_brand_rules():
    msg = StrategyAgent().build_user_message(StrategyInput(brand=BRAND, brief=BRIEF, research=RESEARCH))
    assert f"1. {RESEARCH.findings[0].fact} (sources: weatherspark.com, accuweather.com)" in msg
    assert "- Don't make health claims about coffee" in msg
    assert "angles" not in msg.lower()  # research reports facts only; strategy forms the angle


async def test_strategy_unknown_finding_number_is_rejected_and_retried():
    llm = FakeLLM([plan_json(finding_ids=[3, 42]), plan_json(finding_ids=[3])])
    result = await StrategyAgent().run(StrategyInput(brand=BRAND, brief=BRIEF, research=RESEARCH), llm)
    assert result.attempts == 2 and result.output.finding_ids == [3]
    assert "finding_ids: [42] don't exist" in llm.calls[1][-1].content


@pytest.mark.parametrize("ids, error", [([], "choose 1 to 3"), ([1, 2, 3, 4], "choose 1 to 3"), ([2, 2], "at most once")])
def test_strategy_fact_count_rules(ids, error):
    plan = ContentPlan.model_validate_json(plan_json(finding_ids=ids))
    errors = StrategyAgent().check(plan, StrategyInput(brand=BRAND, brief=BRIEF, research=RESEARCH))
    assert any(error in e for e in errors), errors


def test_approved_facts_are_copied_exactly_from_research():
    plan = ContentPlan.model_validate_json(plan_json(finding_ids=[3, 8]))
    assert plan.approved_facts(RESEARCH) == [RESEARCH.findings[2], RESEARCH.findings[7]]


# --- Content agent with a plan ------------------------------------------------


def test_content_message_includes_plan_and_only_approved_facts():
    plan = ContentPlan.model_validate_json(plan_json(finding_ids=[3]))
    msg = ContentAgent().build_user_message(ContentInput(brand=BRAND, brief=BRIEF, plan=plan, research=RESEARCH))
    assert "# Plan (from the strategist: follow it)" in msg
    assert f"- {RESEARCH.findings[2].fact}" in msg
    assert RESEARCH.findings[4].fact not in msg  # an unselected (health) finding never reaches the writer


def test_content_without_plan_is_unchanged():
    msg = ContentAgent().build_user_message(ContentInput(brand=BRAND, brief=BRIEF))
    assert "# Plan" not in msg


def test_plan_with_findings_requires_research():
    plan = ContentPlan.model_validate_json(plan_json(finding_ids=[3]))
    with pytest.raises(ValueError, match="needs the research report"):
        ContentInput(brand=BRAND, brief=BRIEF, plan=plan)


# --- Pipeline -------------------------------------------------------------------

INR = Pricing(currency="INR", per_1m_tokens=TokenPrices(input=15, output=60))
USD = Pricing(currency="USD", per_1m_tokens=TokenPrices(input=0.10, output=0.40))


def fakes(**replies_by_agent):
    llms = {name: FakeLLM(replies, model=f"fake-{name}") for name, replies in replies_by_agent.items()}
    return llms, (lambda name: llms[name])


async def test_pipeline_with_saved_research_skips_research_agent():
    llms, client_for = fakes(strategy=[plan_json()], content=[DRAFT_JSON])
    tracer = MemoryTracer()
    result = await run_pipeline(BRAND, BRIEF, research=RESEARCH, tracer=tracer, client_for=client_for,
                                pricing_for=lambda name: INR)

    assert result.research_origin == {"research": "file"}
    assert [s.agent for s in result.steps] == ["strategy", "content"]
    assert result.plan.brief_concerns == ["The monsoon has mostly ended by November."]
    assert result.draft.hook.startswith("Chai ya coffee")
    # The writer received the strategist's chosen facts.
    assert RESEARCH.findings[2].fact in llms["content"].calls[0][1].content
    # Each step: 1000 in + 100 out at INR 15/60 per 1M = 0.021
    assert result.totals() == {"INR": pytest.approx(0.042)}

    kinds = [r["type"] for r in tracer.records]
    assert kinds[0] == "pipeline_start" and kinds[-1] == "pipeline_end"
    assert tracer.records[-1]["totals"] == {"INR": pytest.approx(0.042)}


SEARCH_OK = "Cafes launched monsoon drinks."
STRUCTURE = json.dumps({"summary": "s", "findings": [], "trends": []})
# The "research" model serves both layers: topic search + structure, then market search + structure.
LIVE_RESEARCH = [SEARCH_OK, STRUCTURE, SEARCH_OK, STRUCTURE]


async def test_pipeline_costs_each_step_in_its_own_currency(tmp_path):
    _, client_for = fakes(research=list(LIVE_RESEARCH), strategy=[plan_json(finding_ids=[], concerns=[])],
                          content=[DRAFT_JSON])
    tracer = MemoryTracer()
    result = await run_pipeline(BRAND, BRIEF, cache_dir=tmp_path, tracer=tracer, client_for=client_for,
                                pricing_for=lambda name: USD if name == "research" else INR)

    assert [s.agent for s in result.steps] == ["topic_research", "market_research", "strategy", "content"]
    assert result.research_origin == {"topic": "live", "market": "live"}
    assert set(result.totals()) == {"USD", "INR"}  # never added together
    # Every costed event carries the currency of the model that produced it.
    currencies = {r.get("agent") or r.get("attempt"): r["currency"] for r in tracer.records if "currency" in r}
    assert currencies["market_research"] == "USD" and currencies["strategy"] == "INR"


async def test_second_run_reuses_both_research_layers(tmp_path):
    def run_once(research_replies):
        _, client_for = fakes(research=research_replies, strategy=[plan_json(finding_ids=[], concerns=[])],
                              content=[DRAFT_JSON])
        return run_pipeline(BRAND, BRIEF, cache_dir=tmp_path, client_for=client_for, pricing_for=lambda n: USD)

    await run_once(list(LIVE_RESEARCH))
    second = await run_once([])  # an empty reply list: any research call would fail the test

    assert second.research_origin["topic"].startswith("cache") and second.research_origin["market"].startswith("cache")
    assert [s.agent for s in second.steps] == ["strategy", "content"]


async def test_another_brand_in_the_same_market_shares_market_research_only(tmp_path):
    _, client_for = fakes(research=list(LIVE_RESEARCH), strategy=[plan_json(finding_ids=[], concerns=[])],
                          content=[DRAFT_JSON])
    await run_pipeline(BRAND, BRIEF, cache_dir=tmp_path, client_for=client_for, pricing_for=lambda n: USD)

    other_cafe = BRAND.model_copy(update={"name": "Other Cafe"})
    # Only the topic layer may run live: 2 replies (search + structure), none for market.
    _, client_for = fakes(research=[SEARCH_OK, STRUCTURE], strategy=[plan_json(finding_ids=[], concerns=[])],
                          content=[DRAFT_JSON])
    result = await run_pipeline(other_cafe, BRIEF, cache_dir=tmp_path, client_for=client_for,
                                pricing_for=lambda n: USD)
    assert result.research_origin["topic"] == "live"  # private: never reused across brands
    assert result.research_origin["market"].startswith("cache")  # shared


async def test_pipeline_failure_is_traced_with_partial_costs():
    _, client_for = fakes(strategy=[plan_json()], content=[LLMError("quota", status_code=429)])
    tracer = MemoryTracer()
    with pytest.raises(LLMError):
        await run_pipeline(BRAND, BRIEF, research=RESEARCH, tracer=tracer, client_for=client_for,
                           pricing_for=lambda name: INR)
    end = tracer.records[-1]
    assert end["type"] == "pipeline_end" and end["status"] == "error"
    assert [s["agent"] for s in end["steps"]] == ["strategy"]  # what had finished, and its cost
