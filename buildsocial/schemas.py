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
    """What the model writes when structuring search results (the JSON it returns)."""

    summary: str = Field(description="2-3 sentences: what the research found that matters for this post")
    findings: list[Finding] = Field(description="Facts a post could use, each backed by numbered sources")
    trends: list[str] = Field(description="What is current or seasonal and relevant to the topic, from the research")
    angles: list[str] = Field(description="Ideas for how the post could approach the topic; must not add new facts")


class ResearchReport(ResearchNotes):
    """The Research agent's output: the model's notes plus sources and queries added by code."""

    search_queries: list[str]
    sources: list[Source]

    def source(self, source_id: int) -> Source | None:
        return next((s for s in self.sources if s.id == source_id), None)
