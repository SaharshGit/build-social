"""Research agent: brand + brief -> ResearchReport backed by real, numbered web sources.

Two calls, because Gemini can't combine Google Search with JSON output:

  1. SEARCH     the model searches the web and answers in plain text, with citations
  2. STRUCTURE  a standard agent loop turns that answer into ResearchNotes (JSON),
                where each finding refers to sources by number

Sources are numbered and attached by code from the search citations. The model can
only refer to them by number, and check() rejects any finding whose numbers don't
exist, so a finding can't claim a source that wasn't actually found.
"""

import time
from pathlib import Path

from pydantic import BaseModel

from buildsocial.harness.agent import Agent, AgentError, AgentResult
from buildsocial.harness.llm.base import LLMClient, LLMError
from buildsocial.harness.llm.types import Citation, Message, Usage
from buildsocial.harness.trace import Tracer
from buildsocial.schemas import BrandProfile, PostBrief, ResearchNotes, ResearchReport, Source

PROMPTS_DIR = Path(__file__).parent / "prompts"
MAX_FINDINGS = 8
SEARCH_ATTEMPTS = 3
ANGLES_MIN, ANGLES_MAX = 2, 5


class ResearchInput(BaseModel):
    brand: BrandProfile
    brief: PostBrief


class StructureInput(BaseModel):
    brand_name: str
    brand_dos: list[str] = []
    brand_donts: list[str] = []
    brief: PostBrief
    notes: str
    sources: list[Source]


class ResearchStructurer(Agent[StructureInput, ResearchNotes]):
    """Step 2: search answer + numbered sources -> ResearchNotes (standard validate-and-retry loop)."""

    name = "research"  # same model as the search step (agents.research in models.yaml)
    output_type = ResearchNotes
    system_prompt = (PROMPTS_DIR / "research_structure.md").read_text()
    max_output_tokens = 2500  # up to 8 findings + trends + angles

    def build_user_message(self, input: StructureInput) -> str:
        b = input.brief
        if input.sources:
            sources = "\n".join(f'[{s.id}] {s.title or "source"}: "{s.passage or ""}"' for s in input.sources)
        else:
            sources = "(no sources were found, so there can be no findings)"
        rules = [f"- Do: {d}" for d in input.brand_dos] + [f"- Don't: {d}" for d in input.brand_donts]
        return "\n".join([
            "# Brief",
            f"Brand: {input.brand_name}",
            f"Platform: {b.platform}",
            f"Topic: {b.topic}",
            f"Goal: {b.goal}",
            "",
            "# Brand rules (every angle must respect these)",
            "\n".join(rules) or "(none)",
            "",
            "# Research notes",
            input.notes.strip() or "(empty)",
            "",
            "# Sources",
            sources,
        ])

    def check(self, output: ResearchNotes, input: StructureInput) -> list[str]:
        valid = sorted(s.id for s in input.sources)
        errors = []
        if not valid and output.findings:
            errors.append("findings: no sources were found, so findings must be an empty list")
        elif len(output.findings) > MAX_FINDINGS:
            errors.append(f"findings: at most {MAX_FINDINGS} (got {len(output.findings)}); keep the most useful")
        for i, finding in enumerate(output.findings, 1):
            label = f"finding {i} ('{finding.fact[:50]}')"
            if not finding.source_ids:
                errors.append(f"{label}: has no source numbers; cite a source from the list or remove it")
            unknown = [n for n in finding.source_ids if n not in valid]
            if unknown:
                errors.append(f"{label}: source numbers {unknown} don't exist; valid numbers are {valid}")
        if not ANGLES_MIN <= len(output.angles) <= ANGLES_MAX:
            errors.append(f"angles: need {ANGLES_MIN} to {ANGLES_MAX} (got {len(output.angles)})")
        return errors


class ResearchAgent:
    """Runs search, then structuring. Same run() shape as Agent, so callers treat it alike."""

    name = "research"
    search_prompt = (PROMPTS_DIR / "research_search.md").read_text()

    def __init__(self) -> None:
        self.structurer = ResearchStructurer()

    def build_search_message(self, input: ResearchInput) -> str:
        brand, brief = input.brand, input.brief
        lines = [
            f"Research this topic for a {brief.platform} post.",
            "",
            f"Topic: {brief.topic}",
            f"Goal of the post: {brief.goal}",
            f"Notes: {brief.notes}" if brief.notes else None,
            "",
            f"The brand: {brand.name}. {brand.description.strip()}",
            f"Audience: {brand.audience.strip()}",
        ]
        return "\n".join(line for line in lines if line is not None)

    async def run(
        self, input: ResearchInput, llm: LLMClient, tracer: Tracer | None = None
    ) -> AgentResult[ResearchReport]:
        tracer = tracer or Tracer()
        started = time.perf_counter()
        usage = Usage()
        tracer.event("run_start", agent=self.name, input=input)

        try:
            # 1. SEARCH (retried if the reply comes back empty)
            messages = [Message.system(self.search_prompt), Message.user(self.build_search_message(input))]
            for search_attempt in range(1, SEARCH_ATTEMPTS + 1):
                call_started = time.perf_counter()
                try:
                    search = await llm.complete(messages, builtin_tools=["web_search"])
                    break
                except LLMError as e:
                    # Only retry problems with the reply itself (e.g. empty text). HTTP errors such
                    # as quota limits (status_code set) were already retried by the HTTP layer.
                    if not e.retryable or e.status_code is not None or search_attempt == SEARCH_ATTEMPTS:
                        raise
                    tracer.event("search_retry", attempt=search_attempt, error=str(e)[:500])
            usage = usage + search.usage
            tracer.event(
                "llm_call",
                attempt="search",
                messages=messages,
                response=search.model_dump(mode="json", exclude={"usage"}),
                usage=search.usage,
                duration_s=round(time.perf_counter() - call_started, 3),
            )
            sources = number_sources(search.citations)

            # 2. STRUCTURE
            notes = await self.structurer.run(
                StructureInput(
                    brand_name=input.brand.name,
                    brand_dos=input.brand.dos,
                    brand_donts=input.brand.donts,
                    brief=input.brief,
                    notes=search.text or "",
                    sources=sources,
                ),
                llm,
                tracer,
            )
            usage = usage + notes.usage
        except AgentError as e:
            # Report the cost of the WHOLE run, including the search call.
            total = usage + e.usage
            self._end(tracer, started, "failed", total, error=str(e))
            raise AgentError(self.name, e.errors_by_attempt, total) from e
        except Exception as e:
            self._end(tracer, started, "error", usage, error=repr(e))
            raise

        report = ResearchReport(
            **notes.output.model_dump(), search_queries=search.search_queries, sources=sources
        )
        self._end(tracer, started, "ok", usage, output=report, attempts=notes.attempts)
        return AgentResult[ResearchReport](
            output=report, usage=usage, attempts=notes.attempts, model=search.model
        )

    def _end(self, tracer: Tracer, started: float, status: str, usage: Usage, **data) -> None:
        tracer.event(
            "run_end",
            agent=self.name,
            status=status,
            usage=usage,
            duration_s=round(time.perf_counter() - started, 3),
            **data,
        )


def number_sources(citations: list[Citation]) -> list[Source]:
    """Citations -> numbered sources, one per URL (a site cited twice gets one number)."""
    by_url: dict[str, Source] = {}
    for c in citations:
        passage = (c.cited_text or "").strip(" *\n") or None
        if c.url in by_url:
            existing = by_url[c.url]
            if passage and passage != existing.passage:
                existing.passage = f"{existing.passage} … {passage}" if existing.passage else passage
        else:
            by_url[c.url] = Source(id=len(by_url) + 1, title=c.title, url=c.url, passage=passage)
    return list(by_url.values())
