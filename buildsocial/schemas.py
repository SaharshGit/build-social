"""Shared BuildSocial data types: what flows into and between agents.

These are domain types (social media), so they live outside harness/.
Where the data comes from (YAML file today, database/API later) doesn't matter
to agents: they only ever see these objects.
"""

from typing import Literal

from pydantic import BaseModel, Field

Platform = Literal["instagram", "linkedin"]
Goal = Literal["engagement", "awareness", "sales", "community"]
# Content formats. Each needs a skills file (skills/formats/<name>.md). Only text for now;
# carousel, graphic, etc. are added here as they get built.
Format = Literal["text"]


class BrandProfile(BaseModel):
    """A brand's identity. Written once per brand, reused for every post."""

    name: str
    # industry + market decide which SHARED market research a brand uses: every brand
    # with the same pair reads the same weekly market report (see research_cache.py).
    industry: str = Field(description="What kind of business, e.g. 'specialty coffee cafe', 'D2C skincare'")
    market: str = Field(description="Where its customers are, e.g. 'Bengaluru', 'India'")
    description: str = Field(description="What the business does")
    audience: str = Field(description="Who the brand is talking to")
    voice: str = Field(description="Tone and personality, e.g. 'warm, witty, never salesy'")
    language: str = Field(default="English", description="e.g. English, Hinglish, Hindi")
    content_pillars: list[str] = Field(default_factory=list, description="Recurring themes")
    dos: list[str] = Field(default_factory=list)
    donts: list[str] = Field(default_factory=list)
    example_posts: list[str] = Field(default_factory=list, description="Past posts showing the brand's style")


class PostBrief(BaseModel):
    """What we want this time."""

    topic: str
    platform: Platform
    format: Format = "text"
    goal: Goal = "engagement"
    notes: str | None = None


class PostDraft(BaseModel):
    """The Content agent's output. Field descriptions are sent to the LLM as part of the JSON schema."""

    platform: Platform
    hook: str = Field(description="Opening line; must grab attention before the platform's '...more' cut-off")
    body: str = Field(description="Main post text, after the hook")
    call_to_action: str = Field(description="What the reader should do next")
    hashtags: list[str] = Field(description="Each starts with '#', no spaces")
    image_idea: str = Field(description="Short description of the visual to pair with the post")
    rationale: str = Field(description="Briefly, why this post should work for this brand and goal")

    @property
    def full_text(self) -> str:
        """The post as it would be published (used for length checks)."""
        parts = [self.hook, self.body, self.call_to_action, " ".join(self.hashtags)]
        return "\n\n".join(p for p in parts if p)


# ----------------------------------------------------------------------
# Research
# ----------------------------------------------------------------------


class Source(BaseModel):
    """A web source found by search. Created by code from search citations, never by the model."""

    id: int
    title: str | None = None  # usually the site's domain
    url: str  # a Google redirect link for Gemini search results
    passage: str | None = None  # the text this source supported in the search answer


class Finding(BaseModel):
    fact: str = Field(description="One specific, verifiable fact, phrased neutrally")
    source_ids: list[int] = Field(description="Numbers of the sources that support this fact, e.g. [1, 3]")


class ResearchNotes(BaseModel):
    """What the model writes when structuring search results (the JSON it returns).

    Facts only: no angles. Angles depend on the brand, which is the Strategy agent's job.
    """

    summary: str = Field(description="2-3 sentences: what the research found that matters for this post")
    findings: list[Finding] = Field(description="Facts a post could use, each backed by numbered sources")
    trends: list[str] = Field(description="What is current or seasonal and relevant to the topic, from the research")


class ResearchReport(ResearchNotes):
    """The Research agent's output: the model's notes plus sources and queries added by code."""

    search_queries: list[str]
    sources: list[Source]

    def source(self, source_id: int) -> Source | None:
        return next((s for s in self.sources if s.id == source_id), None)


# ----------------------------------------------------------------------
# Strategy
# ----------------------------------------------------------------------


class ContentPlan(BaseModel):
    """The Strategy agent's output: what this one post should say, and which researched facts it may use.

    Field ORDER matters: models write JSON top to bottom. Concerns and reasoning come
    first (think before deciding), and the last field is a short, simple one. With
    `rationale` last, Sarvam once skipped it, couldn't close the JSON in schema mode,
    and wrote blank space until the token limit (2026-11-17).
    """

    brief_concerns: list[str] = Field(
        description="Problems with the brief a human should know about (e.g. it conflicts with the research); "
        "empty if none"
    )
    rationale: str = Field(description="Briefly, which angle and facts suit the brand, goal and platform, and why")
    angle: str = Field(description="The one angle this post takes, in a sentence")
    key_message: str = Field(description="The single message the reader should remember, in one sentence")
    finding_ids: list[int] = Field(
        description="Numbers of the research findings the post should use (1 to 3), from the numbered list"
    )
    cta_direction: str = Field(description="What the call to action should ask the reader to do")

    def approved_facts(self, research: ResearchReport) -> list[Finding]:
        """The exact research findings this plan selected (copied by code, never re-written by a model)."""
        return [research.findings[i - 1] for i in self.finding_ids if 1 <= i <= len(research.findings)]
