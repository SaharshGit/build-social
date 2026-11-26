"""Reviewer evals: does the reviewer catch real problems without raising false alarms?

Hand-written posts with known answers (evals/review_cases.yaml): clean posts (any issue is a false
alarm) and posts with planted problems (each must be flagged). The eval judge (agents/judge.py) is
scored on the same posts, since its false alarms skew every other eval.
"""

import asyncio
import json
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel

from buildsocial.agents.judge import JudgeAgent, JudgeInput
from buildsocial.agents.reviewer import ReviewerAgent, ReviewInput
from buildsocial.harness.llm.base import LLMClient
from buildsocial.harness.llm.registry import Pricing, get_client
from buildsocial.harness.storage import Storage
from buildsocial.harness.trace import Tracer
from buildsocial.pipeline import StepRunner, default_pricing
from buildsocial.revision_evals import STARTS_DIR, Start, _brand, load_start_result
from buildsocial.revision_evals import load as load_starts
from buildsocial.schemas import PostDraft

DEFAULT_CASES = Path("evals/review_cases.yaml")
RESULTS_PREFIX = "evals/results"


class Planted(BaseModel):
    quote: str
    types: list[str]
    judge: str | None = None  # "invented" / "promised": which judge list should catch it


class DraftIn(BaseModel):
    hook: str
    body: str
    call_to_action: str
    hashtags: list[str]


class ReviewCase(BaseModel):
    id: str
    start: str
    clean: bool = False
    draft: DraftIn
    planted: list[Planted] = []


class Flag(BaseModel):
    type: str
    quote: str
    problem: str = ""


class ReviewResult(BaseModel):
    id: str
    clean: bool
    error: str | None = None
    caught: list[str] = []        # planted quotes the reviewer flagged
    missed: list[str] = []        # planted quotes it didn't
    wrong_type: list[str] = []    # caught, but with a type not in the allowed list
    false_alarms: list[Flag] = []  # issues on things that aren't planted problems
    judge_caught: list[str] = []
    judge_missed: list[str] = []
    judge_false_alarms: list[Flag] = []
    cost: dict[str, float] = {}
    seconds: float = 0.0


def _norm(s: str) -> str:
    return " ".join(s.lower().replace("’", "'").split())


def overlaps(issue_quote: str, planted_quote: str) -> bool:
    a, b = _norm(issue_quote), _norm(planted_quote)
    return bool(a) and (b in a or (a in b and len(a) >= 4))


def load(path: Path = DEFAULT_CASES, only: list[str] | None = None) -> list[ReviewCase]:
    cases = [ReviewCase.model_validate(c) for c in yaml.safe_load(path.read_text())["cases"]]
    if only:
        cases = [c for c in cases if c.id in only]
    return cases


async def run_case(case: ReviewCase, start: Start, *, tracer: Tracer | None = None,
                   client_for: Callable[[str], LLMClient] = get_client,
                   pricing_for: Callable[[str], Pricing] = default_pricing,
                   starts_dir: Path = STARTS_DIR) -> ReviewResult:
    began = time.perf_counter()
    run = StepRunner(tracer or Tracer(), client_for, pricing_for)
    try:
        brand = _brand(start)
        base = load_start_result(start, starts_dir)
        draft = PostDraft(platform=start.brief.platform, image_idea="-", rationale="-", **case.draft.model_dump())
        review = await run(ReviewerAgent(), ReviewInput(brand=brand, brief=start.brief, plan=base.plan,
                                                        research=base.research, draft=draft))
        judgement = await run(JudgeAgent(), JudgeInput(brand=brand, brief=start.brief, draft=draft))
    except Exception as e:  # one failing case shouldn't stop the others
        return ReviewResult(id=case.id, clean=case.clean, error=repr(e)[:400])

    r = ReviewResult(id=case.id, clean=case.clean, seconds=round(time.perf_counter() - began, 1))
    for p in case.planted:
        hits = [i for i in review.issues if overlaps(i.quote, p.quote)]
        (r.caught if hits else r.missed).append(p.quote)
        if hits and not any(i.type in p.types for i in hits):
            r.wrong_type.append(f"{p.quote} → {hits[0].type}")
    r.false_alarms = [Flag(type=i.type, quote=i.quote, problem=i.problem) for i in review.issues
                      if not any(overlaps(i.quote, p.quote) for p in case.planted)]

    judge_flags = [Flag(type="invented", quote=q) for q in judgement.invented_product_claims] + \
                  [Flag(type="promised", quote=q) for q in judgement.promised_results]
    for p in case.planted:
        if p.judge:
            hit = any(overlaps(f.quote, p.quote) for f in judge_flags)
            (r.judge_caught if hit else r.judge_missed).append(p.quote)
    r.judge_false_alarms = [f for f in judge_flags if not any(overlaps(f.quote, p.quote) for p in case.planted)]

    for step in run.steps:
        r.cost[step.pricing.currency] = round(r.cost.get(step.pricing.currency, 0.0) + step.cost, 6)
    return r


async def run_eval(cases: list[ReviewCase], *, concurrency: int = 4,
                   tracer_for: Callable[[ReviewCase], Tracer] = lambda case: Tracer(),
                   client_for: Callable[[str], LLMClient] = get_client,
                   pricing_for: Callable[[str], Pricing] = default_pricing) -> list[ReviewResult]:
    starts, _ = load_starts()
    limit = asyncio.Semaphore(concurrency)

    async def one(case: ReviewCase) -> ReviewResult:
        async with limit:
            tracer = tracer_for(case)
            try:
                return await run_case(case, starts[case.start], tracer=tracer, client_for=client_for,
                                      pricing_for=pricing_for)
            finally:
                tracer.close()

    return list(await asyncio.gather(*(one(c) for c in cases)))


def summarize(results: list[ReviewResult]) -> dict:
    ok = [r for r in results if not r.error]
    cost: dict[str, float] = {}
    for r in ok:
        for c, a in r.cost.items():
            cost[c] = round(cost.get(c, 0.0) + a, 6)
    return {
        "cases": len(results), "errors": len(results) - len(ok),
        "planted": sum(len(r.caught) + len(r.missed) for r in ok),
        "caught": sum(len(r.caught) for r in ok),
        "wrong_type": sum(len(r.wrong_type) for r in ok),
        "false_alarms": sum(len(r.false_alarms) for r in ok),
        "clean_posts": sum(r.clean for r in ok),
        "clean_posts_flagged": sum(r.clean and bool(r.false_alarms) for r in ok),
        "judge_planted": sum(len(r.judge_caught) + len(r.judge_missed) for r in ok),
        "judge_caught": sum(len(r.judge_caught) for r in ok),
        "judge_false_alarms": sum(len(r.judge_false_alarms) for r in ok),
        "cost": cost,
    }


def save_results(results: list[ReviewResult], label: str, store: Storage, now: datetime | None = None) -> str:
    now = now or datetime.now()
    key = f"{RESULTS_PREFIX}/{now:%Y-%m-%d_%H%M%S}_reviewer_{label}.json"
    store.write_text(key, json.dumps({"label": label, "kind": "reviewer", "ran_at": now.isoformat(timespec="seconds"),
                                      "summary": summarize(results),
                                      "results": [r.model_dump(mode="json") for r in results]},
                                     indent=2, ensure_ascii=False) + "\n")
    return key


def format_results(results: list[ReviewResult]) -> str:
    lines = [f"{'case':<30} {'caught':>7} {'false alarms':>13} {'judge caught':>13} {'judge false':>12}"]
    for r in results:
        if r.error:
            lines.append(f"{r.id:<30} ERROR {r.error[:80]}")
            continue
        n = len(r.caught) + len(r.missed)
        jn = len(r.judge_caught) + len(r.judge_missed)
        lines.append(f"{r.id:<30} {f'{len(r.caught)}/{n}' if n else '-':>7} {len(r.false_alarms):>13} "
                     f"{f'{len(r.judge_caught)}/{jn}' if jn else '-':>13} {len(r.judge_false_alarms):>12}")
    s = summarize(results)
    cost = ", ".join(f"{c} {a:.4f}" for c, a in s["cost"].items())
    lines += ["", f"REVIEWER caught {s['caught']}/{s['planted']} planted problems (wrong type {s['wrong_type']})  |  "
                  f"false alarms {s['false_alarms']}  |  clean posts flagged {s['clean_posts_flagged']}/{s['clean_posts']}",
              f"JUDGE    caught {s['judge_caught']}/{s['judge_planted']}  |  false alarms {s['judge_false_alarms']}  |  "
              f"cost {cost}", "", "Details:"]
    for r in results:
        lines += [f"  {r.id}: MISSED \"{q}\"" for q in r.missed]
        lines += [f"  {r.id}: wrong type: {w}" for w in r.wrong_type]
        lines += [f"  {r.id}: FALSE ALARM [{f.type}] \"{f.quote}\": {f.problem[:110]}" for f in r.false_alarms]
        lines += [f"  {r.id}: judge missed \"{q}\"" for q in r.judge_missed]
        lines += [f"  {r.id}: judge false alarm [{f.type}] \"{f.quote}\"" for f in r.judge_false_alarms]
    return "\n".join(lines)
