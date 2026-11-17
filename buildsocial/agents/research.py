"""Research: web search turned into sourced, structured findings. Two layers:

  MARKET research   industry + market + date ONLY. Shared by every brand in that market
                    (trends, events, season, what the audience is talking about).
                    Its input type has no brand or brief fields, so private plans can't
                    leak into research that other brands will read.
  TOPIC research    this post's topic. Private to one brand.

Both use the same two-call core, because Gemini can't combine Google Search with JSON
output in one call:

  1. SEARCH     the model searches the web and answers in plain text, with citations
  2. STRUCTURE  a standard agent loop turns that answer into findings (JSON) that refer
                to sources by number; check() rejects numbers that don't exist

Sources are numbered and attached by code, never written by the model. Research no
longer suggests angles: those depend on the brand, which is the Strategy agent's job.
"""

import time
from datetime import date
from pathlib import Path

from pydantic import BaseModel, Field

from buildsocial.harness.agent import Agent, AgentError, AgentResult
from buildsocial.harness.llm.base import LLMClient, LLMError
from buildsocial.harness.llm.types import Citation, Message, Usage
from buildsocial.harness.trace import Tracer
from buildsocial.schemas import Platform, ResearchNotes, ResearchReport, Source

PROMPTS_DIR = Path(__file__).parent / "prompts"
MAX_FINDINGS = 8
SEARCH_ATTEMPTS = 3
MODEL_ALIAS = "research"  # both layers use agents.research in models.yaml


class MarketInput(BaseModel):
    """Everything market research may know. Deliberately NO brand or brief fields."""

    industry: str
    market: str
    today: date = Field(default_factory=date.today)


class TopicInput(BaseModel):
    topic: str
    platform: Platform
    industry: str
    market: str
    notes: str | None = None
    today: date = Field(default_factory=date.today)


# ----------------------------------------------------------------------
# Step 2: structuring (standard validate-and-retry agent loop)
# ----------------------------------------------------------------------


class StructureInput(BaseModel):
    focus: str  # what this research was about, shown to the model
    notes: str  # the search answer
    sources: list[Source]


class ResearchStructurer(Agent[StructureInput, ResearchNotes]):
    name = MODEL_ALIAS
    output_type = ResearchNotes
    system_prompt = (PROMPTS_DIR / "research_structure.md").read_text()
    max_output_tokens = 2500  # up to 8 findings + trends

    def build_user_message(self, input: StructureInput) -> str:
        if input.sources:
            sources = "\n".join(f'[{s.id}] {s.title or "source"}: "{s.passage or ""}"' for s in input.sources)
        else:
            sources = "(no sources were found, so there can be no findings)"
        return "\n".join([
            "# Research focus",
            input.focus,
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
        return errors


# ----------------------------------------------------------------------
# Shared core: search, then structure
# ----------------------------------------------------------------------


class _SourcedResearch:
    """Search + structure. Subclasses supply the prompt, the search request and the focus."""

    name = MODEL_ALIAS  # which model to use (models.yaml)
    label: str  # how this layer appears in traces and step summaries
    search_prompt: str

    def __init__(self) -> None:
        self.structurer = ResearchStructurer()

    def build_search_message(self, input) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    def focus(self, input) -> str:  # pragma: no cover - overridden
        raise NotImplementedError

    async def run(self, input, llm: LLMClient, tracer: Tracer | None = None) -> AgentResult[ResearchReport]:
        tracer = tracer or Tracer()
        started = time.perf_counter()
        usage = Usage()
        tracer.event("run_start", agent=self.label, input=input)

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
                StructureInput(focus=self.focus(input), notes=search.text or "", sources=sources), llm, tracer
            )
            usage = usage + notes.usage
        except AgentError as e:
            # Report the cost of the WHOLE run, including the search call.
            total = usage + e.usage
            self._end(tracer, started, "failed", total, error=str(e))
            raise AgentError(self.label, e.errors_by_attempt, total) from e
        except Exception as e:
            self._end(tracer, started, "error", usage, error=repr(e))
            raise

        report = ResearchReport(
            **notes.output.model_dump(), search_queries=search.search_queries, sources=sources
        )
        self._end(tracer, started, "ok", usage, output=report, attempts=notes.attempts)
        return AgentResult[ResearchReport](output=report, usage=usage, attempts=notes.attempts, model=search.model)

    def _end(self, tracer: Tracer, started: float, status: str, usage: Usage, **data) -> None:
        tracer.event(
            "run_end",
            agent=self.label,
            status=status,
            usage=usage,
            duration_s=round(time.perf_counter() - started, 3),
            **data,
        )


class MarketResearchAgent(_SourcedResearch):
    label = "market_research"
    search_prompt = (PROMPTS_DIR / "research_market.md").read_text()

    def build_search_message(self, input: MarketInput) -> str:
        return "\n".join([
            f"Research the current market for: {input.industry}",
            f"Market: {input.market}",
            f"Today's date: {input.today:%A, %d %B %Y}. Focus on what is current now and in the next few weeks.",
        ])

    def focus(self, input: MarketInput) -> str:
        return f"The current market for {input.industry} in {input.market}, as of {input.today:%d %B %Y}."


class TopicResearchAgent(_SourcedResearch):
    label = "topic_research"
    search_prompt = (PROMPTS_DIR / "research_topic.md").read_text()

    def build_search_message(self, input: TopicInput) -> str:
        lines = [
            f"Research this topic for a {input.platform} post: {input.topic}",
            f"Notes: {input.notes}" if input.notes else None,
            f"Context: a {input.industry} business; audience in {input.market}.",
            f"Today's date: {input.today:%A, %d %B %Y}.",
        ]
        return "\n".join(line for line in lines if line is not None)

    def focus(self, input: TopicInput) -> str:
        return f"The topic '{input.topic}' for a {input.industry} business in {input.market}."


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


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


def merge_reports(topic: ResearchReport, market: ResearchReport) -> ResearchReport:
    """One report for Strategy: topic findings first (most specific), then market.

    Sources are renumbered into one list (a URL found by both layers keeps one number),
    and every finding's source numbers are rewritten to match.
    """
    sources: list[Source] = []
    by_url: dict[str, int] = {}
    findings = []
    for report in (topic, market):
        new_id: dict[int, int] = {}
        for s in report.sources:
            if s.url not in by_url:
                by_url[s.url] = len(sources) + 1
                sources.append(s.model_copy(update={"id": by_url[s.url]}))
            new_id[s.id] = by_url[s.url]
        for f in report.findings:
            findings.append(f.model_copy(update={"source_ids": [new_id[n] for n in f.source_ids if n in new_id]}))

    return ResearchReport(
        summary=f"Topic: {topic.summary}\nMarket: {market.summary}",
        findings=findings,
        trends=list(dict.fromkeys(topic.trends + market.trends)),  # de-duplicated, order kept
        search_queries=topic.search_queries + market.search_queries,
        sources=sources,
    )
