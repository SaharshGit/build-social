"""Shapes of API responses. Requests reuse the agents' own types (e.g. BrandProfile)."""

import uuid
from datetime import datetime

from pydantic import BaseModel

from buildsocial.db.models import Brand
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
