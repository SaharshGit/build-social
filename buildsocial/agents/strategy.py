"""Strategy agent: brand + brief + research -> ContentPlan for one post.

Like research sources, findings are referred to by NUMBER. check() rejects numbers
that don't exist, and the Content agent later receives the exact finding text copied
by code (ContentPlan.approved_facts), so facts can't drift between agents.
"""

from pathlib import Path

from pydantic import BaseModel

from buildsocial.harness.agent import Agent
from buildsocial.schemas import BrandProfile, ContentPlan, PostBrief, ResearchReport

PROMPTS_DIR = Path(__file__).parent / "prompts"
FACTS_MIN, FACTS_MAX = 1, 3
MAX_CONCERNS = 3


class StrategyInput(BaseModel):
    brand: BrandProfile
    brief: PostBrief
    research: ResearchReport


class StrategyAgent(Agent[StrategyInput, ContentPlan]):
    name = "strategy"
    output_type = ContentPlan
    system_prompt = (PROMPTS_DIR / "strategy.md").read_text()
    max_output_tokens = 1500  # a plan is a few hundred tokens

    def build_user_message(self, input: StrategyInput) -> str:
        brand, brief, research = input.brand, input.brief, input.research
        findings = []
        for i, f in enumerate(research.findings, 1):
            sites = ", ".join(s.title or "source" for n in f.source_ids if (s := research.source(n)))
            findings.append(f"{i}. {f.fact} (sources: {sites or 'unknown'})")

        sections = [
            "# Brand",
            f"Name: {brand.name}",
            f"What they do: {brand.description.strip()}",
            f"Audience: {brand.audience.strip()}",
            f"Voice: {brand.voice}",
            _bullets("Content pillars", brand.content_pillars),
            _bullets("Do", brand.dos),
            _bullets("Don't", brand.donts),
            "",
            "# Brief",
            f"Platform: {brief.platform}",
            f"Format: {brief.format}",
            f"Topic: {brief.topic}",
            f"Goal: {brief.goal}",
            f"Notes: {brief.notes}" if brief.notes else None,
            "",
            "# Research",
            f"Summary: {research.summary}",
            "",
            "Findings (choose by number):",
            "\n".join(findings) or "(none)",
            _bullets("Trends", research.trends),
            _bullets("Suggested angles (may be adapted or ignored)", research.angles),
        ]
        return "\n".join(s for s in sections if s is not None).strip()

    def check(self, output: ContentPlan, input: StrategyInput) -> list[str]:
        n_findings = len(input.research.findings)
        valid = list(range(1, n_findings + 1))
        errors = []

        unknown = [i for i in output.finding_ids if i not in valid]
        if unknown:
            errors.append(f"finding_ids: {unknown} don't exist; valid numbers are {valid}")
        if len(set(output.finding_ids)) != len(output.finding_ids):
            errors.append("finding_ids: each number at most once")
        if n_findings and not FACTS_MIN <= len(output.finding_ids) <= FACTS_MAX:
            errors.append(f"finding_ids: choose {FACTS_MIN} to {FACTS_MAX} findings (got {len(output.finding_ids)})")
        if not n_findings and output.finding_ids:
            errors.append("finding_ids: the research has no findings, so this must be an empty list")
        if len(output.brief_concerns) > MAX_CONCERNS:
            errors.append(f"brief_concerns: at most {MAX_CONCERNS}; keep the most important")
        return errors


def _bullets(title: str, items: list[str]) -> str | None:
    if not items:
        return None
    return f"{title}:\n" + "\n".join(f"- {item.strip()}" for item in items)
