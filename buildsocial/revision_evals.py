"""Revision evals: give a fixed post a piece of feedback, revise it, and check what was asked was done.

Scoring per case (see evals/revision_cases.yaml):
  rules     measurable requests, checked by code (feedback_rules)
  requests  the rest, judged by FeedbackCheckAgent (must quote evidence from the new post)
  breakage  platform rules (code), invented product claims / promised results (JudgeAgent)
A case passes when every rule and request is done and nothing broke.
"""

import asyncio
import json
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path

import yaml
from pydantic import BaseModel

from buildsocial.agents.content import ContentAgent, ContentInput
from buildsocial.agents.feedback_check import FeedbackCheckAgent, FeedbackCheckInput
from buildsocial.agents.judge import JudgeAgent, JudgeInput
from buildsocial.feedback_rules import Rule, check_rule
from buildsocial.harness.llm.base import LLMClient
from buildsocial.harness.llm.registry import Pricing, get_client
from buildsocial.harness.storage import Storage
from buildsocial.harness.trace import Tracer
from buildsocial.pipeline import PipelineResult, StepRunner, default_pricing, revise_with_feedback, run_pipeline
from buildsocial.schemas import BrandProfile, ContentPlan, PostBrief, PostDraft, ResearchReport, Review

DEFAULT_CASES = Path("evals/revision_cases.yaml")
STARTS_DIR = Path("evals/revision_starts")
RESULTS_PREFIX = "evals/results"


class Start(BaseModel):
    id: str
    brand: Path
    research: Path
    brief: PostBrief


class RevisionCase(BaseModel):
    id: str
    start: str
    feedback: str
    rules: list[Rule] = []
    requests: list[str] = []


class RuleResult(BaseModel):
    rule: str
    passed: bool
    found: str


class RequestResult(BaseModel):
    request: str
    done: bool
    reason: str
    evidence: str = ""


class RevisionResult(BaseModel):
    id: str
    error: str | None = None
    rules: list[RuleResult] = []
    requests: list[RequestResult] = []
    platform_failures: list[str] = []
    invented: list[str] = []
    promised: list[str] = []
    open_issues: int = 0
    chars_before: int = 0
    chars_after: int = 0
    after: str = ""
    cost: dict[str, float] = {}
    seconds: float = 0.0

    @property
    def done(self) -> int:
        return sum(r.passed for r in self.rules) + sum(r.done for r in self.requests)

    @property
    def asked(self) -> int:
        return len(self.rules) + len(self.requests)

    @property
    def broke(self) -> bool:
        return bool(self.platform_failures or self.invented or self.promised)

    @property
    def passed(self) -> bool:
        return self.error is None and self.done == self.asked and not self.broke


def load(path: Path = DEFAULT_CASES, only: list[str] | None = None) -> tuple[dict[str, Start], list[RevisionCase]]:
    raw = yaml.safe_load(path.read_text())
    starts = {s["id"]: Start.model_validate(s) for s in raw["starts"]}
    cases = [RevisionCase.model_validate(c) for c in raw["cases"]]
    if only:
        unknown = set(only) - {c.id for c in cases}
        if unknown:
            raise KeyError(f"unknown case ids: {sorted(unknown)}")
        cases = [c for c in cases if c.id in only]
    return starts, cases


def _brand(start: Start) -> BrandProfile:
    return BrandProfile.model_validate(yaml.safe_load(start.brand.read_text()))


def _research(start: Start) -> ResearchReport:
    return ResearchReport.model_validate_json(start.research.read_text(encoding="utf-8"))


async def make_start(start: Start, *, tracer: Tracer | None = None,
                     client_for: Callable[[str], LLMClient] = get_client,
                     pricing_for: Callable[[str], Pricing] = default_pricing,
                     directory: Path = STARTS_DIR) -> Path:
    """Generate the starting post once (frozen research) and save it as a fixture."""
    result = await run_pipeline(_brand(start), start.brief, research=_research(start), tracer=tracer,
                                client_for=client_for, pricing_for=pricing_for)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{start.id}.json"
    path.write_text(json.dumps({
        "plan": result.plan.model_dump(mode="json"),
        "draft": result.draft.model_dump(mode="json"),
        "review": result.review.model_dump(mode="json"),
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def load_start_result(start: Start, directory: Path = STARTS_DIR) -> PipelineResult:
    saved = json.loads((directory / f"{start.id}.json").read_text(encoding="utf-8"))
    return PipelineResult(research=_research(start), research_origin={"research": "file"},
                          plan=ContentPlan.model_validate(saved["plan"]), draft=PostDraft.model_validate(saved["draft"]),
                          review=Review.model_validate(saved["review"]))


async def run_case(case: RevisionCase, start: Start, *, tracer: Tracer | None = None,
                   client_for: Callable[[str], LLMClient] = get_client,
                   pricing_for: Callable[[str], Pricing] = default_pricing,
                   starts_dir: Path = STARTS_DIR) -> RevisionResult:
    started = time.perf_counter()
    tracer = tracer or Tracer()
    try:
        brand = _brand(start)
        before = load_start_result(start, starts_dir)
        result = await revise_with_feedback(before, brand, start.brief, case.feedback, tracer=tracer,
                                            client_for=client_for, pricing_for=pricing_for)
        after = result.draft
        checks = StepRunner(tracer, client_for, pricing_for)
        report = None
        if case.requests:
            report = await checks(FeedbackCheckAgent(), FeedbackCheckInput(
                feedback=case.feedback, requests=case.requests, before=before.draft.all_text, after=after.all_text))
        judgement = await checks(JudgeAgent(), JudgeInput(brand=brand, brief=start.brief, draft=after))
    except Exception as e:  # one failing case shouldn't stop the others
        return RevisionResult(id=case.id, error=repr(e)[:500], seconds=round(time.perf_counter() - started, 1))

    rules = []
    for rule in case.rules:
        passed, found = check_rule(rule, after)
        rules.append(RuleResult(rule=rule.describe(), passed=passed, found=found))
    requests = [RequestResult(request=c.request, done=c.done, reason=c.reason, evidence=c.evidence)
                for c in (report.checks if report else [])]
    cost: dict[str, float] = {}
    for step in result.steps + checks.steps:  # the start was loaded from a file, so these are this case's steps only
        cost[step.pricing.currency] = round(cost.get(step.pricing.currency, 0.0) + step.cost, 6)
    return RevisionResult(
        id=case.id, rules=rules, requests=requests,
        platform_failures=ContentAgent().check(after, ContentInput(brand=brand, brief=start.brief)),
        invented=judgement.invented_product_claims, promised=judgement.promised_results,
        open_issues=len(result.review.issues),
        chars_before=len(before.draft.full_text), chars_after=len(after.full_text), after=after.full_text,
        cost=cost, seconds=round(time.perf_counter() - started, 1),
    )


async def run_eval(cases: list[RevisionCase], starts: dict[str, Start], *, concurrency: int = 3,
                   tracer_for: Callable[[RevisionCase], Tracer] = lambda case: Tracer(),
                   client_for: Callable[[str], LLMClient] = get_client,
                   pricing_for: Callable[[str], Pricing] = default_pricing,
                   starts_dir: Path = STARTS_DIR) -> list[RevisionResult]:
    limit = asyncio.Semaphore(concurrency)

    async def one(case: RevisionCase) -> RevisionResult:
        async with limit:
            tracer = tracer_for(case)
            try:
                return await run_case(case, starts[case.start], tracer=tracer, client_for=client_for,
                                      pricing_for=pricing_for, starts_dir=starts_dir)
            finally:
                tracer.close()

    return list(await asyncio.gather(*(one(c) for c in cases)))


def summarize(results: list[RevisionResult]) -> dict:
    cost: dict[str, float] = {}
    for r in results:
        for cur, amount in r.cost.items():
            cost[cur] = round(cost.get(cur, 0.0) + amount, 6)
    return {
        "cases": len(results),
        "passed": sum(r.passed for r in results),
        "requests_done": sum(r.done for r in results),
        "requests_asked": sum(r.asked for r in results),
        "broke_something": sum(r.broke for r in results),
        "errors": sum(r.error is not None for r in results),
        "cost": cost,
    }


def save_results(results: list[RevisionResult], label: str, store: Storage, now: datetime | None = None) -> str:
    now = now or datetime.now()
    key = f"{RESULTS_PREFIX}/{now:%Y-%m-%d_%H%M%S}_revisions_{label}.json"
    record = {"label": label, "kind": "revisions", "ran_at": now.isoformat(timespec="seconds"),
              "summary": summarize(results),
              "results": [{**r.model_dump(mode="json"), "passed": r.passed} for r in results]}
    store.write_text(key, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return key


def format_results(results: list[RevisionResult]) -> str:
    lines = [f"{'case':<28} {'pass':<5} {'done':>5} {'broke':>6} {'chars':>11} {'time':>6}"]
    for r in results:
        if r.error:
            lines.append(f"{r.id:<28} ERROR  {r.error[:90]}")
            continue
        lines.append(f"{r.id:<28} {'✓' if r.passed else '✗':<5} {f'{r.done}/{r.asked}':>5} "
                     f"{'yes' if r.broke else 'no':>6} {f'{r.chars_before}→{r.chars_after}':>11} {r.seconds:>5.0f}s")
    s = summarize(results)
    cost = ", ".join(f"{c} {a:.4f}" for c, a in s["cost"].items())
    lines += ["", f"passed {s['passed']}/{s['cases']}  |  requests done {s['requests_done']}/{s['requests_asked']}  |  "
                  f"broke something {s['broke_something']}  |  cost {cost}", "", "Details:"]
    for r in results:
        if r.error:
            continue
        for x in r.rules:
            lines.append(f"  {r.id}: {'✓' if x.passed else '✗'} {x.rule} ({x.found})")
        for x in r.requests:
            lines.append(f"  {r.id}: {'✓' if x.done else '✗'} {x.request}: {x.reason[:120]}")
        lines += [f"  {r.id}: BROKE platform rule: {f}" for f in r.platform_failures]
        lines += [f"  {r.id}: BROKE invented claim: \"{q}\"" for q in r.invented]
        lines += [f"  {r.id}: BROKE promised result: \"{q}\"" for q in r.promised]
    return "\n".join(lines)
