"""Shapes of API responses. Requests reuse the agents' own types (e.g. BrandProfile)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from buildsocial.db.models import Brand, Conversation, Message, Post, Run
from buildsocial.schemas import BrandProfile, ContentPlan, PostDraft, Review


class BrandSummary(BaseModel):
    """One row in a brand list."""

    id: uuid.UUID
    name: str
    industry: str
    market: str
    products: int
    updated_at: datetime

    @classmethod
    def from_row(cls, b: Brand) -> "BrandSummary":
        return cls(id=b.id, name=b.name, industry=b.industry, market=b.market,
                   products=len(b.profile.get("products", [])), updated_at=b.updated_at)


class BrandOut(BaseModel):
    """One brand in full."""

    id: uuid.UUID
    profile: BrandProfile
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_row(cls, b: Brand) -> "BrandOut":
        return cls(id=b.id, profile=BrandProfile.model_validate(b.profile),
                   created_at=b.created_at, updated_at=b.updated_at)


class Health(BaseModel):
    status: str  # "ok" or "degraded"
    database: str  # "ok" or "unreachable"


class RunOut(BaseModel):
    """A pipeline job and its progress. Poll GET /runs/{id} until status is done or failed."""

    id: uuid.UUID
    brand_id: uuid.UUID
    kind: str
    status: str  # queued -> running -> done / failed
    brief: dict[str, Any]
    current_step: str | None  # the step running now, e.g. "strategy"
    steps: list[dict[str, Any]]  # finished steps: agent, model, attempts, seconds, cost, currency
    cost: dict[str, float]  # totals so far, per currency
    error: str | None
    post_id: uuid.UUID | None  # set when done
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None

    @classmethod
    def from_row(cls, r: Run, post_id: uuid.UUID | None = None) -> "RunOut":
        return cls(id=r.id, brand_id=r.brand_id, kind=r.kind, status=r.status, brief=r.brief,
                   current_step=r.current_step, steps=r.steps, cost=r.cost, error=r.error, post_id=post_id,
                   created_at=r.created_at, started_at=r.started_at, finished_at=r.finished_at)


class PostSummary(BaseModel):
    """One row in the review queue."""

    id: uuid.UUID
    brand_id: uuid.UUID
    status: str  # pending / approved / rejected
    platform: str
    topic: str
    hook: str
    open_issues: int  # reviewer issues still open on the current draft
    revisions: int  # how many earlier drafts there are
    created_at: datetime
    decided_at: datetime | None

    @classmethod
    def from_row(cls, p: Post) -> "PostSummary":
        return cls(id=p.id, brand_id=p.brand_id, status=p.status, platform=p.platform, topic=p.topic,
                   hook=p.draft.get("hook", ""), open_issues=len(p.review.get("issues", [])),
                   revisions=len(p.history), created_at=p.created_at, decided_at=p.decided_at)


class PostOut(BaseModel):
    """One post in full: what a reviewer needs to decide."""

    id: uuid.UUID
    brand_id: uuid.UUID
    run_id: uuid.UUID
    status: str
    platform: str
    topic: str
    full_text: str  # exactly as it would be published
    draft: PostDraft
    review: Review  # open issues on the current draft
    plan: ContentPlan
    facts: list[dict[str, Any]]  # facts the post may use, each with its sources
    history: list[dict[str, Any]]  # earlier drafts, their reviews and the feedback that changed them
    feedback: list[dict[str, Any]]  # what people asked for (revise / approve / reject notes)
    created_at: datetime
    updated_at: datetime
    decided_at: datetime | None

    @classmethod
    def from_row(cls, p: Post) -> "PostOut":
        draft = PostDraft.model_validate(p.draft)
        return cls(id=p.id, brand_id=p.brand_id, run_id=p.run_id, status=p.status, platform=p.platform,
                   topic=p.topic, full_text=draft.full_text, draft=draft, review=Review.model_validate(p.review),
                   plan=ContentPlan.model_validate(p.plan), facts=p.facts, history=p.history,
                   feedback=p.feedback, created_at=p.created_at, updated_at=p.updated_at,
                   decided_at=p.decided_at)


class DecisionIn(BaseModel):
    reason: str | None = Field(default=None, max_length=2000, description="Optional note, kept with the post")


class ReviseIn(BaseModel):
    feedback: str = Field(min_length=3, max_length=2000, description="What should change, in plain words")


class MessageOut(BaseModel):
    id: uuid.UUID
    role: str  # user / assistant
    content: str
    attachments: list[dict[str, Any]]  # e.g. {"type": "run", "id": ...}, {"type": "post", "id": ...}
    created_at: datetime

    @classmethod
    def from_row(cls, m: Message) -> "MessageOut":
        return cls(id=m.id, role=m.role, content=m.content, attachments=m.attachments, created_at=m.created_at)


class ConversationSummary(BaseModel):
    id: uuid.UUID
    title: str
    brand_id: uuid.UUID | None
    updated_at: datetime

    @classmethod
    def from_row(cls, c: Conversation) -> "ConversationSummary":
        return cls(id=c.id, title=c.title, brand_id=c.brand_id, updated_at=c.updated_at)


class ConversationOut(ConversationSummary):
    messages: list[MessageOut]
    created_at: datetime

    @classmethod
    def from_row(cls, c: Conversation) -> "ConversationOut":
        return cls(id=c.id, title=c.title, brand_id=c.brand_id, updated_at=c.updated_at, created_at=c.created_at,
                   messages=[MessageOut.from_row(m) for m in c.messages])


class ConversationIn(BaseModel):
    brand_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=200)


class MessageIn(BaseModel):
    text: str = Field(min_length=1, max_length=4000)


class ChatTurnOut(BaseModel):
    """One exchange: the user's saved message and the assistant's reply."""

    user: MessageOut
    assistant: MessageOut
