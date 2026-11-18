"""Eval runner tests: cases load, code checks, judge quote checks, summaries, compare. No API calls."""

import json
from datetime import datetime
from pathlib import Path

import pytest
import yaml

from buildsocial import evals
from buildsocial.agents.judge import JudgeAgent, JudgeInput, Judgement
from buildsocial.harness.llm.base import LLMClient
from buildsocial.harness.llm.registry import Pricing, TokenPrices
from buildsocial.harness.llm.types import LLMResponse, Usage
from buildsocial.schemas import BrandProfile, ContentPlan, PostBrief, PostDraft, Review

ROOT = Path(__file__).parent.parent
BRAND = BrandProfile.model_validate(yaml.safe_load((ROOT / "examples" / "mamaearth.yaml").read_text()))
BRIEF = PostBrief(topic="onion hair oil for winter hair care", platform="instagram")
DRAFT = PostDraft(platform="instagram", hook="Winter hair, sorted?",
                  body="Our toxin-free Onion Hair Oil is made with onion oil and Redensyl. It keeps hair strong.",
                  call_to_action="Tell us your winter tip!", hashtags=["#Mamaearth", "#Winter", "#Hair"],
                  image_idea="i", rationale="r")


def judgement(invented=(), promised=(), voice=4, useful=3) -> str:
    return json.dumps({"invented_product_claims": list(invented), "promised_results": list(promised),
                       "voice_reason": "v", "voice_score": voice, "usefulness_reason": "u", "usefulness_score": useful})


# --- cases and code checks ----------------------------------------------------------


def test_real_cases_file_loads_and_points_at_existing_files():
    cases = evals.load_cases(ROOT / "evals" / "cases.yaml")
    assert len(cases) == 6 and len({c.id for c in cases}) == 6
    for c in cases:
        assert (ROOT / c.brand).exists() and (ROOT / c.research).exists(), c.id


def test_only_filters_and_rejects_unknown_ids():
    path = ROOT / "evals" / "cases.yaml"
    assert [c.id for c in evals.load_cases(path, ["zerodha-direct-mf-linkedin"])] == ["zerodha-direct-mf-linkedin"]
    with pytest.raises(KeyError, match="unknown case ids"):
        evals.load_cases(path, ["nope"])


def test_code_checks():
    case = evals.EvalCase(id="c", brand=Path("b"), research=Path("r"), brief=BRIEF,
                          must_include=["onion hair oil"], must_not_include=["keeps hair strong", "regrowth"])
    failures = evals.code_checks(case, BRAND, DRAFT)
    assert failures == ["contains forbidden phrase: 'keeps hair strong'"]


# --- judge -------------------------------------------------------------------------


def test_judge_quotes_must_be_in_the_post():
    agent, inp = JudgeAgent(), JudgeInput(brand=BRAND, brief=BRIEF, draft=DRAFT)
    assert agent.check(Judgement.model_validate_json(judgement(promised=["It keeps hair strong."])), inp) == []
    bad = Judgement.model_validate_json(judgement(invented=["contains argan oil"]))
    assert any("is not in the post" in e for e in agent.check(bad, inp))


def test_judge_scores_must_be_1_to_5():
    with pytest.raises(ValueError):
        Judgement.model_validate_json(judgement(voice=7))


# --- running cases with fakes -----------------------------------------------------------

INR = Pricing(currency="INR", per_1m_tokens=TokenPrices(input=10, output=25))
PLAN = ContentPlan(brief_concerns=[], rationale="r", rules_check="c", angle="a", key_message="k",
                   finding_ids=[1], cta_direction="c").model_dump_json()
PASS = Review(issues=[], verdict="pass").model_dump_json()


class FakeLLM(LLMClient):
    def __init__(self, replies):
        self.replies = list(replies)

    async def complete(self, messages, tools=None, output_schema=None, temperature=None, max_tokens=None,
                       builtin_tools=None):
        return LLMResponse(text=self.replies.pop(0), stop_reason="end", usage=Usage(input_tokens=1000), model="fake")


def case(**kw) -> evals.EvalCase:
    defaults = {"id": "mama", "brand": ROOT / "examples" / "mamaearth.yaml",
                "research": ROOT / "evals" / "research" / "mamaearth-onion-oil.json", "brief": BRIEF}
    return evals.EvalCase(**(defaults | kw))


async def test_run_case_scores_a_clean_post():
    replies = {"strategy": [PLAN], "content": [DRAFT.model_dump_json()], "reviewer": [PASS],
               "judge": [judgement(voice=5, useful=4)]}
    r = await evals.run_case(case(must_include=["onion hair oil"]), client_for=lambda n: FakeLLM(replies[n]),
                             pricing_for=lambda n: INR)
    assert r.error is None and r.code_failures == [] and r.passed
    assert r.judgement.voice_score == 5 and r.cost == {"INR": pytest.approx(0.04)}  # 4 calls x 1000 in at INR 10/1M


async def test_promised_result_fails_the_case_and_errors_dont_stop_others():
    replies = {"strategy": [PLAN], "content": [DRAFT.model_dump_json()], "reviewer": [PASS],
               "judge": [judgement(promised=["It keeps hair strong."])]}
    good = case()
    broken = case(id="broken", research=Path("does-not-exist.json"))
    results = await evals.run_eval([good, broken], client_for=lambda n: FakeLLM(replies[n]),
                                   pricing_for=lambda n: INR)

    by_id = {r.id: r for r in results}
    assert not by_id["mama"].passed and by_id["mama"].judgement.promised_results == ["It keeps hair strong."]
    assert by_id["broken"].error and not by_id["broken"].passed
    s = evals.summarize(results)
    assert (s["cases"], s["passed"], s["errors"], s["promised_results"]) == (2, 0, 1, 1)


# --- saving and comparing ------------------------------------------------------------------


def test_save_and_compare(tmp_path):
    j_ok = Judgement.model_validate_json(judgement(voice=3))
    j_bad = Judgement.model_validate_json(judgement(promised=["x"], voice=3))
    a = evals.save_results([evals.CaseResult(id="c1", judgement=j_bad)], "before", tmp_path,
                           now=datetime(2026, 11, 18, 10))
    b = evals.save_results([evals.CaseResult(id="c1", judgement=j_ok)], "after", tmp_path,
                           now=datetime(2026, 11, 18, 11))
    assert a.name == "2026-11-18_100000_before.json"
    out = evals.compare(a, b)
    assert "passed" in out and "0 -> 1" in out
    assert "c1: fail -> pass" in out
