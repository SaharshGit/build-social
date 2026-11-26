"""Post operations: creating posts from finished runs, and the review queue (approve, reject, revise)."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.db.models import Brand, Organization, Post, PostStatus, Run, RunStatus
from buildsocial.pipeline import PipelineResult
from buildsocial.schemas import ContentPlan, Finding, PostDraft, ResearchReport, Review, Source


def facts_with_sources(result: PipelineResult) -> list[dict]:
    """The facts the post was allowed to use, each with its source links (the audit trail)."""
    return [
        {**f.model_dump(mode="json"),
         "sources": [s.model_dump(mode="json") for n in f.source_ids if (s := result.research.source(n))]}
        for f in result.plan.approved_facts(result.research)
    ]


async def create_post_from_result(db: AsyncSession, run: Run, result: PipelineResult) -> Post:
    brief = run.brief
    post = Post(
        brand_id=run.brand_id,
        run_id=run.id,
        status=PostStatus.PENDING,
        platform=brief["platform"],
        topic=brief["topic"],
        draft=result.draft.model_dump(mode="json"),
        review=result.review.model_dump(mode="json"),
        plan=result.plan.model_dump(mode="json"),
        facts=facts_with_sources(result),
        history=[{"draft": d.model_dump(mode="json"), "review": r.model_dump(mode="json")} for d, r in result.history],
        feedback=[],
    )
    db.add(post)
    await db.flush()
    await db.refresh(post)
    return post


# ----------------------------------------------------------------------
# Review queue
# ----------------------------------------------------------------------


class InvalidTransition(Exception):
    """E.g. approving a post that's already approved."""


async def list_posts(
    db: AsyncSession, org: Organization, brand_id: uuid.UUID | None = None, status: str | None = None,
    limit: int = 50,
) -> list[Post]:
    q = select(Post).join(Brand, Post.brand_id == Brand.id).where(Brand.organization_id == org.id)
    if brand_id:
        q = q.where(Post.brand_id == brand_id)
    if status:
        q = q.where(Post.status == status)
    result = await db.execute(q.order_by(Post.created_at.desc()).limit(limit))
    return list(result.scalars())


async def get_post(db: AsyncSession, org: Organization, post_id: uuid.UUID) -> Post | None:
    result = await db.execute(
        select(Post).join(Brand, Post.brand_id == Brand.id).where(Post.id == post_id, Brand.organization_id == org.id)
    )
    return result.scalar_one_or_none()


async def decide(db: AsyncSession, post: Post, status: PostStatus, reason: str | None = None) -> Post:
    """Approve or reject. Only a pending post can be decided."""
    if post.status != PostStatus.PENDING:
        raise InvalidTransition(f"post is already {post.status}")
    post.status = status
    post.decided_at = datetime.now(timezone.utc)
    if reason:
        post.feedback = [*post.feedback, _note("rejected" if status == PostStatus.REJECTED else "approved", reason)]
    await db.flush()
    await db.refresh(post)
    return post


async def active_revision(db: AsyncSession, post_id: uuid.UUID) -> Run | None:
    """A revision of this post that is queued or running (only one at a time)."""
    result = await db.execute(select(Run).where(
        Run.revises_post_id == post_id, Run.status.in_([RunStatus.QUEUED, RunStatus.RUNNING])))
    return result.scalars().first()


def result_from_post(post: Post) -> PipelineResult:
    """Rebuild what the revision step needs from a saved post.

    The post stores the facts it was allowed to use (with sources), so they become a small
    research report and the plan points at them by their new numbers 1..n: the revision
    can use exactly the same facts, and nothing else.
    """
    findings, sources = [], {}
    for f in post.facts:
        for s in f.get("sources", []):
            sources[s["id"]] = Source.model_validate(s)
        findings.append(Finding(fact=f["fact"], scope=f.get("scope", "category"), source_ids=f["source_ids"]))
    research = ResearchReport(summary="", findings=findings, trends=[], search_queries=[],
                              sources=sorted(sources.values(), key=lambda s: s.id))
    plan = ContentPlan.model_validate(post.plan).model_copy(update={"finding_ids": list(range(1, len(findings) + 1))})
    history = [(PostDraft.model_validate(h["draft"]), Review.model_validate(h["review"])) for h in post.history]
    return PipelineResult(research=research, research_origin={"research": "saved post"}, plan=plan,
                          draft=PostDraft.model_validate(post.draft), review=Review.model_validate(post.review),
                          history=history)


async def apply_revision(db: AsyncSession, post: Post, revised: PipelineResult, feedback: str) -> Post:
    """Store a revised draft on the SAME post: the old draft moves into history."""
    post.history = [*post.history, {"draft": post.draft, "review": post.review, "feedback": feedback}]
    post.draft = revised.draft.model_dump(mode="json")
    post.review = revised.review.model_dump(mode="json")
    note = _note("revise", feedback)
    if revised.feedback_checks:  # what was / wasn't done, shown as ✓ / ✗ on the post and in the chat
        note["checks"] = [c.model_dump(mode="json") for c in revised.feedback_checks]
    post.feedback = [*post.feedback, note]
    await db.flush()
    await db.refresh(post)
    return post


def _note(kind: str, text: str) -> dict:
    return {"kind": kind, "text": text, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
