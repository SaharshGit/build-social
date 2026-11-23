"""Shapes of API responses. Requests reuse the agents' own types (e.g. BrandProfile)."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel

from buildsocial.db.models import Brand, Run
from buildsocial.schemas import BrandProfile


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
