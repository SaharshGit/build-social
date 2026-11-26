"""Database tables (SQLAlchemy models): the live state the API and worker work with.

    organizations  the customer account (one for now; every row below belongs to one)
    brands         a brand profile (what examples/*.yaml hold today)
    runs           one pipeline job: the worker's queue and its progress
    posts          the output and its lifecycle (pending -> approved / rejected; later scheduled, published)
    conversations  one chat thread with the BuildSocial assistant
    messages       what was said in a conversation, in order, with attachments (run / post cards)

Large, write-once data (traces, research cache, media) stays in the bucket, not here.
Schema changes go through Alembic migrations (migrations/), never by editing tables by hand.
"""

import uuid
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, DateTime, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

# JSONB on Postgres (stored efficiently, can be indexed later); plain JSON elsewhere (tests).
JsonDoc = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JsonDoc, list[Any]: JsonDoc}


class RunStatus(StrEnum):
    QUEUED = "queued"      # waiting for the worker
    RUNNING = "running"    # the worker is on it
    DONE = "done"          # finished; post_id points at the result
    FAILED = "failed"      # error holds why


class RunKind(StrEnum):
    PIPELINE = "pipeline"  # research -> strategy -> content -> review
    REVISION = "revision"  # a person's feedback on an existing post


class RunTrigger(StrEnum):
    """What started a run (shown in Pipelines). Runs from before this column existed have none."""

    CHAT = "chat"          # the chat assistant (conversation_id says which chat)
    FORM = "form"          # the app's New post form or a post's Revise button
    SCHEDULE = "schedule"  # a schedule (coming)


class PostStatus(StrEnum):
    PENDING = "pending"    # waiting for a person
    APPROVED = "approved"
    REJECTED = "rejected"
    # later: SCHEDULED, PUBLISHED


def new_id() -> uuid.UUID:
    return uuid.uuid4()


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Organization(Timestamps, Base):
    __tablename__ = "organizations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))

    brands: Mapped[list["Brand"]] = relationship(back_populates="organization")


class Brand(Timestamps, Base):
    __tablename__ = "brands"
    __table_args__ = (UniqueConstraint("organization_id", "name"),)  # one "Mamaearth" per organization

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    name: Mapped[str] = mapped_column(String(200))
    industry: Mapped[str] = mapped_column(String(200))  # with market: which shared market research
    market: Mapped[str] = mapped_column(String(200))
    profile: Mapped[dict[str, Any]]  # the full BrandProfile (voice, rules, products...), validated by Pydantic

    organization: Mapped[Organization] = relationship(back_populates="brands")


class Run(Timestamps, Base):
    __tablename__ = "runs"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default=RunKind.PIPELINE)
    # The worker looks for the oldest "queued" run, so this column is indexed.
    status: Mapped[str] = mapped_column(String(20), default=RunStatus.QUEUED, index=True)
    brief: Mapped[dict[str, Any]]  # the PostBrief
    feedback: Mapped[str | None] = mapped_column(Text)  # revision runs: what the person asked for
    # Revision runs: the post being revised. (use_alter: posts also point back at runs.)
    revises_post_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("posts.id", use_alter=True, name="fk_runs_revises_post_id")
    )
    current_step: Mapped[str | None] = mapped_column(String(50))  # progress, e.g. "strategy"
    steps: Mapped[list[Any]] = mapped_column(default=list)  # finished steps: agent, model, seconds, cost
    cost: Mapped[dict[str, Any]] = mapped_column(default=dict)  # totals per currency
    error: Mapped[str | None] = mapped_column(Text)
    trace_key: Mapped[str | None] = mapped_column(String(300))  # where the full trace is in storage
    trigger: Mapped[str | None] = mapped_column(String(20))  # RunTrigger; None for runs from before it existed
    # Chat runs: the chat that asked for it. Deleting the chat keeps the run.
    conversation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("conversations.id", ondelete="SET NULL", name="fk_runs_conversation_id"))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Post(Timestamps, Base):
    __tablename__ = "posts"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    brand_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("brands.id"), index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("runs.id"))  # the run that produced it
    status: Mapped[str] = mapped_column(String(20), default=PostStatus.PENDING, index=True)
    platform: Mapped[str] = mapped_column(String(30))
    topic: Mapped[str] = mapped_column(Text)
    draft: Mapped[dict[str, Any]]  # PostDraft: hook, body, call to action, hashtags, image idea
    review: Mapped[dict[str, Any]]  # the latest Review (open issues)
    plan: Mapped[dict[str, Any]]  # ContentPlan
    facts: Mapped[list[Any]] = mapped_column(default=list)  # facts used, with their sources
    history: Mapped[list[Any]] = mapped_column(default=list)  # earlier drafts and their reviews
    feedback: Mapped[list[Any]] = mapped_column(default=list)  # what people asked to change
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # approved/rejected when


class MessageRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class Conversation(Timestamps, Base):
    __tablename__ = "conversations"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    organization_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("organizations.id"), index=True)
    # The brand this chat is about (chosen at the top of the chat; the assistant can switch it).
    brand_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("brands.id"))
    title: Mapped[str] = mapped_column(String(200), default="New chat")

    messages: Mapped[list["Message"]] = relationship(
        back_populates="conversation", order_by="Message.created_at", cascade="all, delete-orphan"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=new_id)
    conversation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("conversations.id"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # user / assistant
    content: Mapped[str] = mapped_column(Text)
    # Rich cards shown with the message, e.g. [{"type": "run", "id": "..."}, {"type": "post", "id": "..."}]
    attachments: Mapped[list[Any]] = mapped_column(default=list)
    # Stamped in Python, not by the database: Postgres's now() is the same for everything in one
    # transaction, so a question and its answer saved together would tie and could swap order.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc), server_default=func.now()
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
