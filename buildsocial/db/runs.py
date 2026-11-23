"""Run operations: the job queue between the API (creates runs) and the worker (processes them)."""

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.db.models import Brand, Organization, Post, Run, RunKind, RunStatus
from buildsocial.schemas import PostBrief


def now() -> datetime:
    return datetime.now(timezone.utc)


async def create_run(
    db: AsyncSession,
    brand: Brand,
    brief: PostBrief,
    *,
    kind: RunKind = RunKind.PIPELINE,
    feedback: str | None = None,
    revises_post_id: uuid.UUID | None = None,
) -> Run:
    run = Run(brand_id=brand.id, kind=kind, brief=brief.model_dump(mode="json"), feedback=feedback,
              revises_post_id=revises_post_id, status=RunStatus.QUEUED)
    db.add(run)
    await db.flush()
    await db.refresh(run)
    return run


async def get_run(db: AsyncSession, org: Organization, run_id: uuid.UUID) -> Run | None:
    # Join through the brand so a run is only visible to its own organization.
    result = await db.execute(
        select(Run).join(Brand, Run.brand_id == Brand.id).where(Run.id == run_id, Brand.organization_id == org.id)
    )
    return result.scalar_one_or_none()


async def list_runs(
    db: AsyncSession, org: Organization, brand_id: uuid.UUID | None = None, status: str | None = None,
    limit: int = 50,
) -> list[Run]:
    q = select(Run).join(Brand, Run.brand_id == Brand.id).where(Brand.organization_id == org.id)
    if brand_id:
        q = q.where(Run.brand_id == brand_id)
    if status:
        q = q.where(Run.status == status)
    result = await db.execute(q.order_by(Run.created_at.desc()).limit(limit))
    return list(result.scalars())


async def post_id_for(db: AsyncSession, run: Run) -> uuid.UUID | None:
    """The post a run produced, or (revision runs) the post it revised, once it's done."""
    if run.revises_post_id is not None:
        return run.revises_post_id if run.status == RunStatus.DONE else None
    result = await db.execute(select(Post.id).where(Post.run_id == run.id).order_by(Post.created_at.desc()).limit(1))
    return result.scalar_one_or_none()


async def claim_next_run(db: AsyncSession) -> Run | None:
    """Take the oldest queued run and mark it running, so no other worker takes it.

    FOR UPDATE locks the chosen row; SKIP LOCKED makes other workers skip it instead of
    waiting. (SQLite, used in tests, ignores these; it only ever has one writer.)
    The caller must commit straight away so the claim becomes visible.
    """
    result = await db.execute(
        select(Run)
        .where(Run.status == RunStatus.QUEUED)
        .order_by(Run.created_at)
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    run = result.scalar_one_or_none()
    if run is None:
        return None
    run.status = RunStatus.RUNNING
    run.started_at = now()
    run.current_step = None
    await db.flush()
    return run


async def record_progress(db: AsyncSession, run_id: uuid.UUID, label: str, summary: dict[str, Any] | None) -> None:
    """A step started (summary None) or finished (summary = its agent, model, time, cost)."""
    run = await db.get(Run, run_id)
    if run is None:
        return
    if summary is None:
        run.current_step = label
    else:
        run.steps = [*run.steps, summary]  # new list: JSON columns only save when reassigned
        cost = dict(run.cost)
        cost[summary["currency"]] = round(cost.get(summary["currency"], 0.0) + summary["cost"], 6)
        run.cost = cost
    await db.flush()


async def finish_run(db: AsyncSession, run_id: uuid.UUID, trace_key: str | None) -> None:
    await db.execute(update(Run).where(Run.id == run_id).values(
        status=RunStatus.DONE, current_step=None, finished_at=now(), trace_key=trace_key))


async def fail_run(db: AsyncSession, run_id: uuid.UUID, error: str, trace_key: str | None) -> None:
    await db.execute(update(Run).where(Run.id == run_id).values(
        status=RunStatus.FAILED, error=error[:4000], finished_at=now(), trace_key=trace_key))


async def fail_stale_runs(db: AsyncSession, older_than: timedelta = timedelta(minutes=15)) -> int:
    """Runs stuck in 'running' (their worker crashed or was stopped) are marked failed.

    A normal run takes well under a minute, so 15 minutes means nobody is working on it.
    Returns how many were failed.
    """
    result = await db.execute(
        update(Run)
        .where(Run.status == RunStatus.RUNNING, Run.started_at < now() - older_than)
        .values(status=RunStatus.FAILED, error="worker stopped before finishing (marked failed on restart)",
                finished_at=now())
    )
    return result.rowcount or 0
