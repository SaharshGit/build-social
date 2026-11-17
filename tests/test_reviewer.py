"""Reviewer agent, Content revisions, and the review loop in the pipeline. Fake LLMs: no API calls."""

import json
from pathlib import Path

import pytest
import yaml

from buildsocial.agents.content import ContentAgent, ContentInput, Revision
from buildsocial.agents.reviewer import ReviewerAgent, ReviewInput, quote_in
from buildsocial.harness.llm.base import LLMClient
from buildsocial.harness.llm.registry import Pricing, TokenPrices
from buildsocial.harness.llm.types import LLMResponse, Usage
from buildsocial.pipeline import run_pipeline
from buildsocial.schemas import BrandProfile, ContentPlan, PostBrief, PostDraft, ResearchReport, Review, ReviewIssue

ROOT = Path(__file__).parent.parent
BRAND = BrandProfile.model_validate(yaml.safe_load((ROOT / "examples" / "mamaearth.yaml").read_text()))
RESEARCH = ResearchReport.model_validate_json(
    (ROOT / "examples" / "research" / "brew-house-masala-chai.json").read_text(encoding="utf-8")
)
BRIEF = PostBrief(topic="onion hair oil for winter hair care", platform="instagram")
PLAN = ContentPlan(
    brief_concerns=[], rationale="r", angle="Winter hair tip", key_message="Oil before you wash.",
    finding_ids=[3], cta_direction="Ask for their winter tip",
)

DRAFT = PostDraft(
    platform="instagram",
    hook="Winter mein hair dry ho raha hai?",
    body="A gentle oil massage before washing helps. Humare formula mein essential oils hain for a pleasant smell.",
    call_to_action="Comment your favourite winter hair tip!",
    hashtags=["#Mamaearth", "#WinterHairCare", "#OnionHairOil"],
    image_idea="x",
    rationale="y",
)
INVENTED = "Humare formula mein essential oils hain for a pleasant smell"


def review_json(issues=(), verdict=None) -> str:
    issues = [
        {"type": t, "quote": q, "problem": "p", "fix": "Remove this claim."} for t, q in issues
    ]
    return json.dumps({"issues": issues, "verdict": verdict or ("revise" if issues else "pass")})


def review_input(draft=DRAFT) -> ReviewInput:
    return ReviewInput(brand=BRAND, brief=BRIEF, plan=PLAN, research=RESEARCH, draft=draft)


# --- Reviewer agent ---------------------------------------------------------------


def test_reviewer_sees_rules_approved_facts_and_the_draft():
    msg = ReviewerAgent().build_user_message(review_input())
    assert "- No medical or therapeutic claims" in msg  # brand don'ts
    assert f"- {RESEARCH.findings[2].fact}" in msg  # only the approved facts
    assert RESEARCH.findings[4].fact not in msg
    assert f"BODY: {DRAFT.body}" in msg


def test_issue_must_quote_the_draft_exactly():
    agent = ReviewerAgent()
    real = Review.model_validate_json(review_json([("invented_fact", INVENTED)]))
    assert agent.check(real, review_input()) == []
    # Case and line-wrapping differences are fine; made-up text is not.
    wrapped = Review.model_validate_json(review_json([("invented_fact", "humare formula mein\n essential oils")]))
    assert agent.check(wrapped, review_input()) == []
    made_up = Review.model_validate_json(review_json([("risky_claim", "cures all hair problems")]))
    assert any("is not in the draft" in e for e in agent.check(made_up, review_input()))


@pytest.mark.parametrize("issues, verdict, error", [
    ([("invented_fact", INVENTED)], "pass", "is 'pass' but issues were listed"),
    ([], "revise", "is 'revise' but no issues"),
])
def test_verdict_must_match_issues(issues, verdict, error):
    review = Review.model_validate_json(review_json(issues, verdict))
    assert any(error in e for e in ReviewerAgent().check(review, review_input()))


def test_quote_in_ignores_case_and_spacing():
    assert quote_in("Essential  OILS", "with essential\noils inside")
    assert not quote_in("rose oil", "with essential oils inside")


# --- Content revision ---------------------------------------------------------------


def revision_input() -> ContentInput:
    issue = ReviewIssue(type="invented_fact", quote=INVENTED, problem="Not in the brief.", fix="Remove it.")
    return ContentInput(brand=BRAND, brief=BRIEF, plan=PLAN, research=RESEARCH,
                        revision=Revision(previous=DRAFT, issues=[issue]))


def test_revision_prompt_shows_previous_draft_and_issues():
    msg = ContentAgent().build_user_message(revision_input())
    assert "# Revise your previous draft" in msg
    assert f"BODY: {DRAFT.body}" in msg
    assert f'1. [invented_fact] "{INVENTED}": Not in the brief. Fix: Remove it.' in msg


def test_revision_must_remove_flagged_text():
    agent = ContentAgent()
    unchanged = agent.check(DRAFT, revision_input())
    assert any("was flagged (invented_fact) and is still in the draft" in e for e in unchanged)
    fixed = DRAFT.model_copy(update={"body": "A gentle oil massage before washing helps."})
    assert agent.check(fixed, revision_input()) == []


# --- the review loop in the pipeline ---------------------------------------------------

INR = Pricing(currency="INR", per_1m_tokens=TokenPrices(input=15, output=60))
PLAN_JSON = PLAN.model_dump_json()
DRAFT_JSON = DRAFT.model_dump_json()
FIXED_JSON = DRAFT.model_copy(update={"body": "A gentle oil massage before washing helps."}).model_dump_json()


class FakeLLM(LLMClient):
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def complete(self, messages, tools=None, output_schema=None, temperature=None, max_tokens=None,
                       builtin_tools=None):
        self.calls.append(messages)
        return LLMResponse(text=self.replies.pop(0), stop_reason="end", usage=Usage(input_tokens=100),
                           model="fake")


def run(strategy, content, reviewer):
    llms = {"strategy": FakeLLM(strategy), "content": FakeLLM(content), "reviewer": FakeLLM(reviewer)}
    result = run_pipeline(BRAND, BRIEF, research=RESEARCH, client_for=lambda n: llms[n], pricing_for=lambda n: INR)
    return result, llms


async def test_clean_draft_is_reviewed_once():
    result, llms = await run([PLAN_JSON], [DRAFT_JSON], [review_json()])[0], None
    assert result.review.verdict == "pass" and result.history == []
    assert [s.agent for s in result.steps] == ["strategy", "content", "reviewer"]


async def test_flagged_draft_is_revised_once_and_re_reviewed():
    coro, llms = run([PLAN_JSON], [DRAFT_JSON, FIXED_JSON], [review_json([("invented_fact", INVENTED)]), review_json()])
    result = await coro

    assert [s.agent for s in result.steps] == ["strategy", "content", "reviewer", "revision_1", "review_2"]
    assert result.draft.body == "A gentle oil massage before washing helps."
    assert result.review.verdict == "pass"
    assert len(result.history) == 1 and result.history[0][1].issues[0].quote == INVENTED
    # The revision prompt carried the reviewer's issue.
    assert "# Revise your previous draft" in llms["content"].calls[1][1].content


async def test_issues_left_after_one_revision_are_returned_not_hidden():
    still_bad = review_json([("risky_claim", "helps")])
    coro, _ = run([PLAN_JSON], [DRAFT_JSON, FIXED_JSON], [review_json([("invented_fact", INVENTED)]), still_bad])
    result = await coro
    assert result.review.verdict == "revise" and result.review.issues[0].quote == "helps"
    assert len([s for s in result.steps if s.agent.startswith("revision")]) == 1  # no endless loop


def test_prompts_put_brand_rules_above_plan_and_fact_wording():
    from buildsocial.agents import content, reviewer, strategy

    assert "Brand rules beat everything, including the plan and the facts" in content.ContentAgent.system_prompt
    assert "No promised results in the key message" in strategy.StrategyAgent.system_prompt
    assert "Never invent offers or mechanics" in strategy.StrategyAgent.system_prompt
    assert "The plan can be wrong too" in reviewer.ReviewerAgent.system_prompt
    msg = ContentAgent().build_user_message(ContentInput(brand=BRAND, brief=BRIEF, plan=PLAN, research=RESEARCH))
    assert "phrase them within the brand rules" in msg
