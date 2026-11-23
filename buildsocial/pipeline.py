"""Prototype pipeline: Research (market + topic) -> Strategy -> Content -> Review for one post.

If the review finds issues, the Content agent revises once and the draft is reviewed again.
Issues still left after that are returned (for a human to decide), not hidden.

Plain code decides the order (not an LLM). Each agent gets its own model from
models.yaml, and each step's cost is recorded in that model's own currency
(research runs on Gemini in USD, the others on Sarvam in INR), so totals are kept
per currency instead of adding rupees to dollars.

Research is the only paid step that can be skipped: each layer is reused from the
cache when possible (market: shared, per week; topic: per brand, up to 7 days).

This lives in buildsocial/, not harness/, because the steps are BuildSocial's. A
generic harness pipeline can be extracted once a second pipeline shows what it needs.
"""

import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field, replace
from datetime import date
from typing import Any

from buildsocial import research_cache
from buildsocial.agents.content import ContentAgent, ContentInput, Revision
from buildsocial.agents.research import (
    MarketInput,
    MarketResearchAgent,
    TopicInput,
    TopicResearchAgent,
    merge_reports,
)
from buildsocial.agents.reviewer import ReviewerAgent, ReviewInput
from buildsocial.agents.strategy import StrategyAgent, StrategyInput
from buildsocial.harness.agent import AgentResult
from buildsocial.harness.llm.base import LLMClient
from buildsocial.harness.llm.registry import Pricing, get_client, resolve_model
from buildsocial.harness.storage import Storage
from buildsocial.harness.trace import Tracer
from buildsocial.schemas import BrandProfile, ContentPlan, PostBrief, PostDraft, ResearchReport, Review

DEFAULT_MAX_REVISIONS = 1  # automatic review -> revise rounds; compare alternatives with evals


def default_pricing(model_alias: str) -> Pricing:
    return resolve_model(model_alias)[1].pricing


@dataclass
class StepRecord:
    agent: str
    result: AgentResult
    pricing: Pricing
    seconds: float

    @property
    def cost(self) -> float:
        return self.pricing.cost(self.result.usage)

    def summary(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "model": self.result.model,
            "attempts": self.result.attempts,
            "seconds": round(self.seconds, 2),
            "cost": round(self.cost, 6),
            "currency": self.pricing.currency,
        }


@dataclass
class PipelineResult:
    research: ResearchReport
    research_origin: dict[str, str]  # layer -> "live", "cache (1.2 days old)", or "file"
    plan: ContentPlan
    draft: PostDraft  # the final draft (revised, if a revision happened)
    review: Review  # the review of the final draft; its issues are still open
    history: list[tuple[PostDraft, Review]] = field(default_factory=list)  # earlier drafts and their reviews
    steps: list[StepRecord] = field(default_factory=list)

    def totals(self) -> dict[str, float]:
        return _totals(self.steps)


# Called when a step starts (summary=None) and when it finishes (summary = StepRecord.summary()).
# The worker uses it to write live progress into the runs table; the CLI doesn't need it.
ProgressFn = Callable[[str, dict[str, Any] | None], Awaitable[None]]


class StepRunner:
    """Runs agents with the right client, pricing and tracer, and records each step."""

    def __init__(
        self,
        tracer: Tracer,
        client_for: Callable[[str], LLMClient],
        pricing_for: Callable[[str], Pricing],
        on_progress: ProgressFn | None = None,
    ):
        self.tracer, self.client_for, self.pricing_for = tracer, client_for, pricing_for
        self.on_progress = on_progress
        self.steps: list[StepRecord] = []

    async def __call__(self, agent: Any, agent_input: Any, label: str | None = None) -> Any:
        label = label or getattr(agent, "label", agent.name)  # how the step is shown (e.g. market_research)
        if self.on_progress:
            await self.on_progress(label, None)
        pricing = self.pricing_for(agent.name)  # agent.name = which model (models.yaml)
        started = time.perf_counter()
        async with self.client_for(agent.name) as llm:
            result = await agent.run(agent_input, llm, self.tracer.scoped(pricing.cost, pricing.currency))
        record = StepRecord(label, result, pricing, time.perf_counter() - started)
        self.steps.append(record)
        if self.on_progress:
            await self.on_progress(label, record.summary())
        return result.output


async def gather_research(
    brand: BrandProfile,
    brief: PostBrief,
    run: StepRunner,
    *,
    cache: Storage | None,
    fresh: bool = False,
    today: date | None = None,
) -> tuple[ResearchReport, dict[str, str]]:
    """Market + topic research, each from the cache when possible, merged into one report."""
    today = today or date.today()
    market_in = MarketInput(industry=brand.industry, market=brand.market, today=today)
    topic_in = TopicInput(
        topic=brief.topic, platform=brief.platform, industry=brand.industry, market=brand.market,
        notes=brief.notes, today=today,
    )
    origin: dict[str, str] = {}

    async def layer(name, agent, agent_input, load, save) -> ResearchReport:
        hit = None if (fresh or cache is None) else load()
        if hit:
            cached, key = hit
            origin[name] = f"cache ({cached.age_days():.1f} days old)"
            run.tracer.event("research_cache", layer=name, hit=True, key=key, age_days=round(cached.age_days(), 2))
            return cached.report
        report = await run(agent, agent_input)
        origin[name] = "live"
        if cache is not None:
            key = save(report)
            run.tracer.event("research_cache", layer=name, hit=False, saved_to=key)
        return report

    topic = await layer(
        "topic", TopicResearchAgent(), topic_in,
        lambda: research_cache.load_topic(cache, brand.name, topic_in),
        lambda r: research_cache.save_topic(cache, brand.name, topic_in, r),
    )
    market = await layer(
        "market", MarketResearchAgent(), market_in,
        lambda: research_cache.load_market(cache, market_in),
        lambda r: research_cache.save_market(cache, market_in, r),
    )
    return merge_reports(topic, market), origin


async def run_pipeline(
    brand: BrandProfile,
    brief: PostBrief,
    *,
    research: ResearchReport | None = None,
    cache: Storage | None = None,  # no caching unless asked: tests can never write fake research to the real cache
    fresh_research: bool = False,
    today: date | None = None,
    max_revisions: int = DEFAULT_MAX_REVISIONS,
    tracer: Tracer | None = None,
    client_for: Callable[[str], LLMClient] = get_client,
    pricing_for: Callable[[str], Pricing] = default_pricing,
    on_progress: ProgressFn | None = None,
) -> PipelineResult:
    """Run the agents in order.

    research:       a saved report to use instead of researching (e.g. from a file)
    cache:          storage for the research cache; None (the default) disables it. The CLI passes its storage.
    fresh_research: ignore cached research and search again (the result is still saved)
    client_for / pricing_for: model alias -> LLM client / prices; tests pass fakes.
    """
    tracer = tracer or Tracer()
    run = StepRunner(tracer, client_for, pricing_for, on_progress)
    started = time.perf_counter()
    tracer.event("pipeline_start", brand=brand.name, brief=brief, research_from_file=research is not None)

    try:
        if research is not None:
            origin = {"research": "file"}
        else:
            research, origin = await gather_research(
                brand, brief, run, cache=cache, fresh=fresh_research, today=today
            )
        plan = await run(StrategyAgent(), StrategyInput(brand=brand, brief=brief, research=research,
                                                        **({"today": today} if today else {})))
        content_in = ContentInput(brand=brand, brief=brief, plan=plan, research=research)
        draft = await run(ContentAgent(), content_in)
        review = await run(
            ReviewerAgent(), ReviewInput(brand=brand, brief=brief, plan=plan, research=research, draft=draft)
        )
        history: list[tuple[PostDraft, Review]] = []
        for n in range(1, max_revisions + 1):
            if review.verdict == "pass":
                break
            history.append((draft, review))
            revision = Revision(previous=draft, issues=review.issues)
            draft = await run(ContentAgent(), content_in.model_copy(update={"revision": revision}), label=f"revision_{n}")
            review = await run(
                ReviewerAgent(), ReviewInput(brand=brand, brief=brief, plan=plan, research=research, draft=draft),
                label=f"review_{n + 1}",
            )
    except Exception as e:
        tracer.event(
            "pipeline_end",
            status="error",
            error=repr(e)[:500],
            steps=[s.summary() for s in run.steps],
            totals=_totals(run.steps),
            seconds=round(time.perf_counter() - started, 2),
        )
        raise

    result = PipelineResult(
        research=research, research_origin=origin, plan=plan, draft=draft, review=review, history=history,
        steps=run.steps,
    )
    tracer.event(
        "pipeline_end",
        status="ok",
        research_origin=origin,
        revisions=len(history),
        open_issues=len(review.issues),
        steps=[s.summary() for s in run.steps],
        totals=result.totals(),
        seconds=round(time.perf_counter() - started, 2),
    )
    return result


def _totals(steps: list[StepRecord]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for s in steps:
        totals[s.pricing.currency] = round(totals.get(s.pricing.currency, 0.0) + s.cost, 6)
    return totals


async def revise_with_feedback(
    result: PipelineResult,
    brand: BrandProfile,
    brief: PostBrief,
    feedback: str,
    *,
    tracer: Tracer | None = None,
    client_for: Callable[[str], LLMClient] = get_client,
    pricing_for: Callable[[str], Pricing] = default_pricing,
    on_progress: ProgressFn | None = None,
) -> PipelineResult:
    """Apply a person's feedback: revise the draft (also fixing any still-open review issues), review again.

    Same path as the reviewer's revisions, so the code checks and the re-review still apply.
    """
    tracer = tracer or Tracer()
    run = StepRunner(tracer, client_for, pricing_for, on_progress)
    round_no = sum(1 for s in result.steps if s.agent.startswith("feedback_revision")) + 1
    tracer.event("human_feedback", round=round_no, feedback=feedback)

    revision = Revision(previous=result.draft, issues=result.review.issues, human_feedback=feedback)
    content_in = ContentInput(brand=brand, brief=brief, plan=result.plan, research=result.research, revision=revision)
    draft = await run(ContentAgent(), content_in, label=f"feedback_revision_{round_no}")
    review = await run(
        ReviewerAgent(),
        ReviewInput(brand=brand, brief=brief, plan=result.plan, research=result.research, draft=draft),
        label=f"feedback_review_{round_no}",
    )
    return replace(
        result,
        draft=draft,
        review=review,
        history=result.history + [(result.draft, result.review)],
        steps=result.steps + run.steps,
    )
