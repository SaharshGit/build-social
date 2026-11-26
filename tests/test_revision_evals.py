"""Revision evals and feedback checks with scripted fake LLMs: no API calls, no cost."""

import json
from pathlib import Path

from buildsocial import revision_evals as rev
from buildsocial.agents.feedback_check import FeedbackCheckAgent, FeedbackCheckInput, FeedbackReport, RequestCheck
from buildsocial.feedback_rules import Rule, check_rule
from buildsocial.schemas import PostDraft
from tests.test_api_runs import DRAFT, INR, PASS, PLAN, scripted

EXAMPLES = Path(__file__).parent.parent / "examples"
EVALS = Path(__file__).parent.parent / "evals"


def draft(**kw) -> PostDraft:
    base = dict(platform="instagram", hook="Baarish wali shaam?", body="Cosy corner, warm cup.",
                call_to_action="Come say hi this week.", hashtags=["#BrewHouse", "#Bengaluru", "#Cafe"],
                image_idea="i", rationale="r")
    return PostDraft(**{**base, **kw})


def test_rules():
    d = draft(body="Cosy corner ☕ and masala chai.")
    assert check_rule(Rule(kind="max_chars", value="1000"), d)[0]
    assert not check_rule(Rule(kind="max_chars", value="20"), d)[0]
    assert check_rule(Rule(kind="max_hashtags", value="3"), d)[0]
    assert not check_rule(Rule(kind="max_hashtags", value="2"), d)[0]
    assert not check_rule(Rule(kind="no_emojis"), d)[0]
    assert check_rule(Rule(kind="no_emojis"), draft(body="₹180 only"))[0]  # ₹ is not an emoji
    assert check_rule(Rule(kind="hook_is_question"), draft(hook="Rainy evening? ☕"))[0]  # trailing emoji ignored
    assert not check_rule(Rule(kind="ends_with_question"), d)[0]
    assert check_rule(Rule(kind="must_include", value="MASALA chai"), d)[0]
    assert not check_rule(Rule(kind="must_not_include", value="chai"), d)[0]


def test_feedback_check_needs_real_evidence():
    agent = FeedbackCheckAgent()
    inp = FeedbackCheckInput(feedback="more Hinglish", requests=["more Hinglish"], before="Hello there.",
                             after="Kya baat hai, chai time!")
    ok = FeedbackReport(checks=[RequestCheck(request="more Hinglish", reason="adds Hindi", evidence="Kya baat hai",
                                             done=True)])
    assert agent.check(ok, inp) == []
    made_up = ok.model_copy(update={"checks": [ok.checks[0].model_copy(update={"evidence": "Arre yaar"})]})
    assert "not in the AFTER post" in agent.check(made_up, inp)[0]
    assert "exactly 1 checks" in agent.check(FeedbackReport(checks=[ok.checks[0], ok.checks[0]]), inp)[0]


def write_start(tmp_path: Path) -> rev.Start:
    start = rev.Start(id="brew", brand=EXAMPLES / "brew-house.yaml", research=EVALS / "research/brew-house-masala-chai.json",
                      brief={"topic": "monsoon", "platform": "instagram", "goal": "engagement"})
    (tmp_path / "brew.json").write_text(json.dumps({
        "plan": json.loads(PLAN), "draft": json.loads(DRAFT), "review": json.loads(PASS)}))
    return start


JUDGE_OK = json.dumps({"invented_product_claims": [], "promised_results": [], "voice_reason": "r", "voice_score": 4,
                       "usefulness_reason": "r", "usefulness_score": 4})


async def test_case_scores_rules_requests_and_breakage(tmp_path):
    start = write_start(tmp_path)
    revised = json.loads(DRAFT) | {"hook": "Chai pe charcha?", "body": "Yaar, ek cup aur?"}
    case = rev.RevisionCase(id="c", start="brew", feedback="hook as a question, more Hinglish",
                            rules=[Rule(kind="hook_is_question"), Rule(kind="max_chars", value="10")],
                            requests=["more Hinglish"])
    check = json.dumps({"checks": [{"request": "more Hinglish", "reason": "adds yaar", "evidence": "Yaar, ek cup aur?",
                                    "done": True}]})
    client_for, _ = scripted(content=[json.dumps(revised)], reviewer=[PASS], feedback_check=[check], judge=[JUDGE_OK])
    r = await rev.run_case(case, start, client_for=client_for, pricing_for=lambda n: INR, starts_dir=tmp_path)

    assert r.error is None
    assert [x.passed for x in r.rules] == [True, False]  # a question ✓, under 10 characters ✗
    assert r.requests[0].done and r.done == 2 and r.asked == 3
    assert not r.broke and not r.passed  # one request missed -> the case fails
    assert r.cost["INR"] > 0
