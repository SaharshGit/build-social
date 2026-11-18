"""Human approval: feedback revisions, the approve/revise/save/quit loop, and saved files. No API calls."""

import json
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from buildsocial.agents.content import ContentAgent, ContentInput, Revision
from buildsocial.cli import approval_loop
from buildsocial.harness.llm.base import LLMClient
from buildsocial.harness.llm.registry import Pricing, TokenPrices
from buildsocial.harness.llm.types import LLMResponse, Usage
from buildsocial.harness.storage import MemoryStorage
from buildsocial.harness.trace import MemoryTracer
from buildsocial.output import save_post
from buildsocial.pipeline import PipelineResult, revise_with_feedback
from buildsocial.schemas import BrandProfile, ContentPlan, PostBrief, PostDraft, ResearchReport, Review

ROOT = Path(__file__).parent.parent
BRAND = BrandProfile.model_validate(yaml.safe_load((ROOT / "examples" / "mamaearth.yaml").read_text()))
RESEARCH = ResearchReport.model_validate_json(
    (ROOT / "examples" / "research" / "brew-house-masala-chai.json").read_text(encoding="utf-8")
)
BRIEF = PostBrief(topic="onion hair oil for winter hair care", platform="instagram")
PLAN = ContentPlan(brief_concerns=[], rationale="r", rules_check="c", angle="a", key_message="k",
                   finding_ids=[3], cta_direction="c")
DRAFT = PostDraft(platform="instagram", hook="Winter hair, sorted?", body="Our Onion Hair Oil is toxin-free.",
                  call_to_action="Tell us your winter tip!", hashtags=["#Mamaearth", "#Winter", "#Hair"],
                  image_idea="i", rationale="r")
SHORTER = DRAFT.model_copy(update={"body": "Our Onion Hair Oil: toxin-free, made with onion oil and Redensyl."})
PASS = Review(issues=[], verdict="pass")
INR = Pricing(currency="INR", per_1m_tokens=TokenPrices(input=10, output=25))


def base_result() -> PipelineResult:
    return PipelineResult(research=RESEARCH, research_origin={"research": "file"}, plan=PLAN, draft=DRAFT,
                          review=PASS)


class FakeLLM(LLMClient):
    def __init__(self, replies):
        self.replies = list(replies)
        self.calls = []

    async def complete(self, messages, tools=None, output_schema=None, temperature=None, max_tokens=None,
                       builtin_tools=None):
        self.calls.append(messages)
        return LLMResponse(text=self.replies.pop(0), stop_reason="end", usage=Usage(input_tokens=1000),
                           model="fake")


# --- feedback revisions --------------------------------------------------------------


def test_human_feedback_goes_into_the_revision_prompt():
    rev = Revision(previous=DRAFT, human_feedback="Make it shorter and mention Redensyl.")
    msg = ContentAgent().build_user_message(ContentInput(brand=BRAND, brief=BRIEF, plan=PLAN, research=RESEARCH,
                                                         revision=rev))
    assert "Feedback from the person approving this post (most important):" in msg
    assert "Make it shorter and mention Redensyl." in msg
    assert "Issues found by the editor:" not in msg  # none were given


def test_revision_needs_issues_or_feedback():
    with pytest.raises(ValueError, match="needs reviewer issues or human feedback"):
        Revision(previous=DRAFT)


async def test_revise_with_feedback_rewrites_then_reviews_again():
    llms = {"content": FakeLLM([SHORTER.model_dump_json()]), "reviewer": FakeLLM([PASS.model_dump_json()])}
    tracer = MemoryTracer()
    result = await revise_with_feedback(base_result(), BRAND, BRIEF, "Mention Redensyl.", tracer=tracer,
                                        client_for=lambda n: llms[n], pricing_for=lambda n: INR)

    assert result.draft == SHORTER and result.review.verdict == "pass"
    assert result.history == [(DRAFT, PASS)]  # the earlier draft is kept
    assert [s.agent for s in result.steps] == ["feedback_revision_1", "feedback_review_1"]
    assert "Mention Redensyl." in llms["content"].calls[0][1].content
    assert any(r["type"] == "human_feedback" for r in tracer.records)


# --- the approval loop ------------------------------------------------------------------


def scripted(*answers):
    answers = list(answers)

    def ask(prompt):
        if not answers:
            raise EOFError
        return answers.pop(0)

    return ask


@pytest.mark.parametrize("answers, status", [(["a"], "approved"), (["s"], "pending"), (["q"], None), ([], "pending")])
async def test_loop_choices(answers, status):
    _, got, feedback = await approval_loop(base_result(), BRAND, BRIEF, MemoryTracer(), ask=scripted(*answers))
    assert got == status and feedback == []


async def test_loop_revises_with_feedback_then_approves():
    calls = []

    async def fake_revise(result, brand, brief, text, tracer):
        calls.append(text)
        return PipelineResult(**{**result.__dict__, "draft": SHORTER})

    result, status, feedback = await approval_loop(
        base_result(), BRAND, BRIEF, MemoryTracer(), ask=scripted("x", "r", "Shorter please", "a"),
        revise=fake_revise,
    )
    assert status == "approved" and feedback == ["Shorter please"] and calls == ["Shorter please"]
    assert result.draft == SHORTER


# --- saved files ---------------------------------------------------------------------------


def test_save_post_writes_markdown_and_full_json():
    store = MemoryStorage()
    md, js = save_post(store, BRAND, BRIEF, base_result(), "approved", feedback=["Shorter"],
                       now=datetime(2026, 11, 17, 21, 30))
    assert md == "output/mamaearth/2026-11-17_213000_instagram_onion-hair-oil-for-winter-hair-care.md"

    text = store.read_text(md)
    assert text.startswith("# Mamaearth · instagram · APPROVED")
    assert DRAFT.full_text in text
    assert RESEARCH.findings[2].tagged() in text and "](https://" in text  # facts with source links
    assert "1. Shorter" in text

    record = json.loads(store.read_text(js))
    assert record["status"] == "approved" and record["draft"]["hook"] == DRAFT.hook
    assert record["facts_used"][0]["sources"][0]["url"].startswith("https://")
