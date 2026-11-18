"""Evals: run fixed cases through the pipeline and score them, to compare changes.

Each case uses FROZEN research (no Gemini credit, same facts every run). Scoring:
  code checks (free)  platform rules, must_include / must_not_include phrases
  judge (LLM, cheap)  invented product claims, promised results (as verified quotes),
                      voice and usefulness scores (1-5)

A case PASSES when the code checks pass and the judge found no invented product claims
and no promised results. Scores are reported as averages, for comparing runs.
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
from buildsocial.agents.judge import JudgeAgent, JudgeInput, Judgement
from buildsocial.harness.llm.base import LLMClient
from buildsocial.harness.llm.registry import Pricing, get_client
from buildsocial.harness.trace import Tracer
from buildsocial.pipeline import StepRunner, default_pricing, run_pipeline
from buildsocial.schemas import BrandProfile, PostBrief, PostDraft, ResearchReport

DEFAULT_CASES = Path("evals/cases.yaml")
RESULTS_DIR = Path("evals/results")


class EvalCase(BaseModel):
    id: str
    brand: Path
    research: Path
    brief: PostBrief
    must_include: list[str] = []
    must_not_include: list[str] = []


class CaseResult(BaseModel):
    id: str
    error: str | None = None  # the case couldn't run (API failure etc.)
    code_failures: list[str] = []
    judgement: Judgement | None = None
    open_issues: int = 0  # reviewer issues left on the final draft
    revisions: int = 0
    cost: dict[str, float] = {}
    seconds: float = 0.0
    draft: PostDraft | None = None

    @property
    def passed(self) -> bool:
        j = self.judgement
        return (
            self.error is None
            and not self.code_failures
            and j is not None
            and not j.invented_product_claims
            and not j.promised_results
        )


def load_cases(path: Path = DEFAULT_CASES, only: list[str] | None = None) -> list[EvalCase]:
    raw = yaml.safe_load(path.read_text())
    cases = [EvalCase.model_validate(c) for c in raw["cases"]]
    if only:
        unknown = set(only) - {c.id for c in cases}
        if unknown:
            raise KeyError(f"unknown case ids: {sorted(unknown)}")
        cases = [c for c in cases if c.id in only]
    return cases


def code_checks(case: EvalCase, brand: BrandProfile, draft: PostDraft) -> list[str]:
    """Free, exact checks on the final post."""
    failures = ContentAgent().check(draft, ContentInput(brand=brand, brief=case.brief))  # platform rules
    text = draft.full_text.lower()
    failures += [f"missing required phrase: '{p}'" for p in case.must_include if p.lower() not in text]
    failures += [f"contains forbidden phrase: '{p}'" for p in case.must_not_include if p.lower() in text]
    return failures


async def run_case(
    case: EvalCase,
    *,
    max_revisions: int = 1,
    tracer: Tracer | None = None,
    client_for: Callable[[str], LLMClient] = get_client,
    pricing_for: Callable[[str], Pricing] = default_pricing,
) -> CaseResult:
    started = time.perf_counter()
    tracer = tracer or Tracer()
    try:
        brand = BrandProfile.model_validate(yaml.safe_load(case.brand.read_text()))
        research = ResearchReport.model_validate_json(case.research.read_text(encoding="utf-8"))
        result = await run_pipeline(
            brand, case.brief, research=research, tracer=tracer, client_for=client_for, pricing_for=pricing_for,
            max_revisions=max_revisions,
        )
        judge_run = StepRunner(tracer, client_for, pricing_for)
        judgement = await judge_run(JudgeAgent(), JudgeInput(brand=brand, brief=case.brief, draft=result.draft))
    except Exception as e:  # one failing case shouldn't stop the others
        return CaseResult(id=case.id, error=repr(e)[:500], seconds=round(time.perf_counter() - started, 1))

    cost: dict[str, float] = {}
    for step in result.steps + judge_run.steps:
        cost[step.pricing.currency] = round(cost.get(step.pricing.currency, 0.0) + step.cost, 6)
    return CaseResult(
        id=case.id,
        code_failures=code_checks(case, brand, result.draft),
        judgement=judgement,
        open_issues=len(result.review.issues),
        revisions=len(result.history),
        cost=cost,
        seconds=round(time.perf_counter() - started, 1),
        draft=result.draft,
    )


async def run_eval(
    cases: list[EvalCase],
    *,
    concurrency: int = 3,
    max_revisions: int = 1,
    tracer_for: Callable[[EvalCase], Tracer] = lambda case: Tracer(),
    client_for: Callable[[str], LLMClient] = get_client,
    pricing_for: Callable[[str], Pricing] = default_pricing,
) -> list[CaseResult]:
    """Run cases a few at a time (calls mostly wait on the network; Sarvam sometimes hangs)."""
    limit = asyncio.Semaphore(concurrency)

    async def one(case: EvalCase) -> CaseResult:
        async with limit:
            return await run_case(case, max_revisions=max_revisions, tracer=tracer_for(case),
                                  client_for=client_for, pricing_for=pricing_for)

    return list(await asyncio.gather(*(one(c) for c in cases)))


# ----------------------------------------------------------------------
# Results: save, summarize, compare
# ----------------------------------------------------------------------


def summarize(results: list[CaseResult]) -> dict:
    judged = [r.judgement for r in results if r.judgement]
    cost: dict[str, float] = {}
    for r in results:
        for cur, amount in r.cost.items():
            cost[cur] = round(cost.get(cur, 0.0) + amount, 6)
    avg = lambda xs: round(sum(xs) / len(xs), 2) if xs else None  # noqa: E731
    return {
        "cases": len(results),
        "passed": sum(r.passed for r in results),
        "errors": sum(r.error is not None for r in results),
        "avg_voice": avg([j.voice_score for j in judged]),
        "avg_usefulness": avg([j.usefulness_score for j in judged]),
        "invented_product_claims": sum(len(j.invented_product_claims) for j in judged),
        "promised_results": sum(len(j.promised_results) for j in judged),
        "code_failures": sum(len(r.code_failures) for r in results),
        "open_review_issues": sum(r.open_issues for r in results),
        "cost": cost,
    }


def save_results(results: list[CaseResult], label: str, directory: Path = RESULTS_DIR,
                 now: datetime | None = None) -> Path:
    now = now or datetime.now()
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{now:%Y-%m-%d_%H%M%S}_{label}.json"
    record = {
        "label": label,
        "ran_at": now.isoformat(timespec="seconds"),
        "summary": summarize(results),
        "results": [{**r.model_dump(mode="json"), "passed": r.passed} for r in results],
    }
    path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def format_results(results: list[CaseResult]) -> str:
    lines = [f"{'case':<30} {'pass':<5} {'voice':>5} {'useful':>6} {'invented':>8} {'promised':>8} "
             f"{'code':>4} {'open':>4} {'time':>6}"]
    for r in results:
        if r.error:
            lines.append(f"{r.id:<30} ERROR  {r.error[:80]}")
            continue
        j = r.judgement
        lines.append(
            f"{r.id:<30} {'✓' if r.passed else '✗':<5} {j.voice_score:>5} {j.usefulness_score:>6} "
            f"{len(j.invented_product_claims):>8} {len(j.promised_results):>8} {len(r.code_failures):>4} "
            f"{r.open_issues:>4} {r.seconds:>5.0f}s"
        )
    s = summarize(results)
    cost = ", ".join(f"{c} {a:.4f}" for c, a in s["cost"].items())
    lines += [
        "",
        f"passed {s['passed']}/{s['cases']}  |  avg voice {s['avg_voice']}  |  avg usefulness {s['avg_usefulness']}  |  "
        f"invented claims {s['invented_product_claims']}  |  promised results {s['promised_results']}  |  "
        f"code failures {s['code_failures']}  |  open review issues {s['open_review_issues']}  |  cost {cost}",
    ]
    details = []
    for r in results:
        if r.code_failures:
            details += [f"  {r.id}: {f}" for f in r.code_failures]
        if r.judgement:
            details += [f"  {r.id}: invented → \"{q}\"" for q in r.judgement.invented_product_claims]
            details += [f"  {r.id}: promised → \"{q}\"" for q in r.judgement.promised_results]
    if details:
        lines += ["", "Problems found:"] + details
    return "\n".join(lines)


def compare(a_path: Path, b_path: Path) -> str:
    a, b = (json.loads(p.read_text()) for p in (a_path, b_path))
    lines = [f"A: {a['label']} ({a['ran_at']})", f"B: {b['label']} ({b['ran_at']})", ""]
    for key in ("passed", "avg_voice", "avg_usefulness", "invented_product_claims", "promised_results",
                "code_failures", "open_review_issues"):
        va, vb = a["summary"][key], b["summary"][key]
        delta = "" if va is None or vb is None else f"  ({vb - va:+.2f})"
        lines.append(f"  {key:<24} {va!s:>6} -> {vb!s:<6}{delta}")
    lines.append(f"  {'cost':<24} {a['summary']['cost']} -> {b['summary']['cost']}")
    a_cases = {r["id"]: r for r in a["results"]}
    changed = [
        f"  {r['id']}: {'pass' if a_cases[r['id']]['passed'] else 'fail'} -> {'pass' if r['passed'] else 'fail'}"
        for r in b["results"] if r["id"] in a_cases and a_cases[r["id"]]["passed"] != r["passed"]
    ]
    if changed:
        lines += ["", "Cases that changed:"] + changed
    return "\n".join(lines)
