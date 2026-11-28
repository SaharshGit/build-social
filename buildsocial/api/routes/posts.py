"""Post endpoints: the review queue. A post starts "pending"; a person approves it, rejects it,
or asks for a revision (which a worker applies to the SAME post, keeping the old draft in history)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.api.deps import current_org, get_db, get_store
from buildsocial.api.schemas import DecisionIn, PostOut, PostSummary, ReviseIn, RunOut
from buildsocial.db import posts as post_ops
from buildsocial.db import runs as run_ops
from buildsocial.db.models import Brand, Organization, Post, PostStatus, Run, RunKind, RunTrigger
from buildsocial.harness.storage import Storage
from buildsocial.schemas import PostBrief

router = APIRouter(prefix="/posts", tags=["posts"])


async def _post_or_404(db: AsyncSession, org: Organization, post_id: uuid.UUID) -> Post:
    post = await post_ops.get_post(db, org, post_id)
    if post is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "post not found")
    return post


@router.get("", response_model=list[PostSummary])
async def list_posts(
    brand_id: uuid.UUID | None = None, status: PostStatus | None = None, limit: int = 50,
    db: AsyncSession = Depends(get_db), org: Organization = Depends(current_org),
):
    """Newest first. ?status=pending is the review queue."""
    return [PostSummary.from_row(p) for p in await post_ops.list_posts(db, org, brand_id, status, min(limit, 200))]


@router.get("/{post_id}", response_model=PostOut)
async def get_post(post_id: uuid.UUID, db: AsyncSession = Depends(get_db), org: Organization = Depends(current_org)):
    return PostOut.from_row(await _post_or_404(db, org, post_id))


async def _decide(db, org, post_id, new_status, body: DecisionIn | None) -> PostOut:
    post = await _post_or_404(db, org, post_id)
    if await post_ops.active_revision(db, post.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "a revision of this post is in progress; wait for it")
    try:
        post = await post_ops.decide(db, post, new_status, body.reason if body else None)
    except post_ops.InvalidTransition as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    return PostOut.from_row(post)


@router.post("/{post_id}/approve", response_model=PostOut)
async def approve(post_id: uuid.UUID, body: DecisionIn | None = None,
                  db: AsyncSession = Depends(get_db), org: Organization = Depends(current_org)):
    return await _decide(db, org, post_id, PostStatus.APPROVED, body)


@router.post("/{post_id}/reject", response_model=PostOut)
async def reject(post_id: uuid.UUID, body: DecisionIn | None = None,
                 db: AsyncSession = Depends(get_db), org: Organization = Depends(current_org)):
    return await _decide(db, org, post_id, PostStatus.REJECTED, body)


@router.post("/{post_id}/revise", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def revise(post_id: uuid.UUID, body: ReviseIn,
                 db: AsyncSession = Depends(get_db), org: Organization = Depends(current_org)) -> RunOut:
    """Queue a revision with your feedback. Returns the revision run (poll GET /runs/{id});
    when it's done, the same post has the new draft and the old one is in its history."""
    post = await _post_or_404(db, org, post_id)
    if post.status != PostStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"only pending posts can be revised (this one is {post.status})")
    if await post_ops.active_revision(db, post.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "a revision of this post is already in progress")
    brand = await db.get(Brand, post.brand_id)
    original = await db.get(Run, post.run_id)
    run = await run_ops.create_run(db, brand, PostBrief.model_validate(original.brief), kind=RunKind.REVISION,
                                   feedback=body.feedback, revises_post_id=post.id, trigger=RunTrigger.FORM)
    return RunOut.from_row(run)


@router.get("/{post_id}/image", response_class=Response,
            responses={200: {"content": {"image/png": {}}}})
async def get_image(post_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                    org: Organization = Depends(current_org), store: Storage = Depends(get_store)) -> Response:
    """The post's designed image (a PNG). Posts without one (text posts, or image making failed) answer 404."""
    post = await _post_or_404(db, org, post_id)
    key = (post.image or {}).get("key")
    if not key:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "this post has no image")
    try:
        data = store.read_bytes(key)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "the image file is missing")
    # Each version has its own URL (?v=), so it can be cached; private: it belongs to one organization.
    return Response(data, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=86400", "X-Content-Type-Options": "nosniff"})


@router.post("/{post_id}/image", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def redesign_image(post_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                         org: Organization = Depends(current_org)) -> RunOut:
    """Design the image again with a different layout (also retries after a failure). Queues a run: poll
    GET /runs/{id}; when it's done the post has the new image."""
    post = await _post_or_404(db, org, post_id)
    original = await db.get(Run, post.run_id)
    brief = PostBrief.model_validate(original.brief)
    if brief.format != "single_image":
        raise HTTPException(status.HTTP_409_CONFLICT, "only image posts have an image")
    if post.status != PostStatus.PENDING:
        raise HTTPException(status.HTTP_409_CONFLICT, f"only pending posts can change (this one is {post.status})")
    if await post_ops.active_revision(db, post.id):
        raise HTTPException(status.HTTP_409_CONFLICT, "this post is already being changed")
    brand = await db.get(Brand, post.brand_id)
    run = await run_ops.create_run(db, brand, brief, kind=RunKind.IMAGE, revises_post_id=post.id,
                                   trigger=RunTrigger.FORM)
    return RunOut.from_row(run)
