"""Run endpoints: start a pipeline run (returns at once) and follow its progress."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.api.deps import current_org, get_db
from buildsocial.api.schemas import RunOut
from buildsocial.db import brands as brand_ops
from buildsocial.db import runs as run_ops
from buildsocial.db.models import Organization, RunStatus
from buildsocial.schemas import PostBrief

router = APIRouter(tags=["runs"])


@router.post("/brands/{brand_id}/runs", response_model=RunOut, status_code=status.HTTP_202_ACCEPTED)
async def start_run(
    brand_id: uuid.UUID, brief: PostBrief,
    db: AsyncSession = Depends(get_db), org: Organization = Depends(current_org),
) -> RunOut:
    """Queue a pipeline run for this brand. Returns immediately with status "queued";
    a worker (`buildsocial worker`) picks it up. Poll GET /runs/{id} for progress."""
    brand = await brand_ops.get_brand(db, org, brand_id)
    if brand is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand not found")
    run = await run_ops.create_run(db, brand, brief)
    return RunOut.from_row(run)


@router.get("/runs/{run_id}", response_model=RunOut)
async def get_run(
    run_id: uuid.UUID, db: AsyncSession = Depends(get_db), org: Organization = Depends(current_org)
) -> RunOut:
    run = await run_ops.get_run(db, org, run_id)
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "run not found")
    return RunOut.from_row(run, await run_ops.post_id_for(db, run.id))


@router.get("/runs", response_model=list[RunOut])
async def list_runs(
    brand_id: uuid.UUID | None = None, status: RunStatus | None = None, limit: int = 50,
    db: AsyncSession = Depends(get_db), org: Organization = Depends(current_org),
):
    """Most recent first. Filter by brand and/or status (e.g. ?status=queued)."""
    runs = await run_ops.list_runs(db, org, brand_id, status, min(limit, 200))
    return [RunOut.from_row(r, await run_ops.post_id_for(db, r.id)) for r in runs]
