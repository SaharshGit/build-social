"""Prototype pipeline: Research -> Strategy -> Content for one post.

Plain code decides the order (not an LLM). Each agent gets its own model from
models.yaml, and each step's cost is recorded in that model's own currency
(research runs on Gemini in USD, the others on Sarvam in INR), so totals are kept
per currency instead of adding rupees to dollars.

This lives in buildsocial/, not harness/, because the steps are BuildSocial's. A
generic harness pipeline can be extracted once a second pipeline shows what it needs.
"""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from buildsocial.agents.content import ContentAgent, ContentInput
from buildsocial.agents.research import ResearchAgent, ResearchInput
from buildsocial.agents.strategy import StrategyAgent, StrategyInput
from buildsocial.harness.agent import AgentResult
from buildsocial.harness.llm.base import LLMClient
from buildsocial.harness.llm.registry import Pricing, get_client, resolve_model
from buildsocial.harness.trace import Tracer
from buildsocial.schemas import BrandProfile, ContentPlan, PostBrief, PostDraft, ResearchReport


def default_pricing(agent_name: str) -> Pricing:
    return resolve_model(agent_name)[1].pricing


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
    research_from_file: bool
    plan: ContentPlan
    draft: PostDraft
    steps: list[StepRecord] = field(default_factory=list)

    def totals(self) -> dict[str, float]:
        return _totals(self.steps)


async def run_pipeline(
    brand: BrandProfile,
    brief: PostBrief,
    *,
    research: ResearchReport | None = None,
    tracer: Tracer | None = None,
    client_for: Callable[[str], LLMClient] = get_client,
    pricing_for: Callable[[str], Pricing] = default_pricing,
) -> PipelineResult:
    """Run the agents in order. Pass `research` to skip the Research agent (e.g. a saved report).

    client_for / pricing_for map an agent name to its LLM client / prices; tests pass fakes.
    """
    tracer = tracer or Tracer()
    steps: list[StepRecord] = []
    started = time.perf_counter()
    tracer.event("pipeline_start", brand=brand.name, brief=brief, research_from_file=research is not None)

    async def step(agent: Any, agent_input: Any) -> Any:
        pricing = pricing_for(agent.name)
        step_started = time.perf_counter()
        async with client_for(agent.name) as llm:
            result = await agent.run(agent_input, llm, tracer.scoped(pricing.cost, pricing.currency))
        steps.append(StepRecord(agent.name, result, pricing, time.perf_counter() - step_started))
        return result.output

    try:
        from_file = research is not None
        if research is None:
            research = await step(ResearchAgent(), ResearchInput(brand=brand, brief=brief))
        plan = await step(StrategyAgent(), StrategyInput(brand=brand, brief=brief, research=research))
        draft = await step(
            ContentAgent(), ContentInput(brand=brand, brief=brief, plan=plan, research=research)
        )
    except Exception as e:
        tracer.event(
            "pipeline_end",
            status="error",
            error=repr(e)[:500],
            steps=[s.summary() for s in steps],
            totals=_totals(steps),
            seconds=round(time.perf_counter() - started, 2),
        )
        raise

    result = PipelineResult(research=research, research_from_file=from_file, plan=plan, draft=draft, steps=steps)
    tracer.event(
        "pipeline_end",
        status="ok",
        steps=[s.summary() for s in steps],
        totals=result.totals(),
        seconds=round(time.perf_counter() - started, 2),
    )
    return result


def _totals(steps: list[StepRecord]) -> dict[str, float]:
    totals: dict[str, float] = {}
    for s in steps:
        totals[s.pricing.currency] = round(totals.get(s.pricing.currency, 0.0) + s.cost, 6)
    return totals
