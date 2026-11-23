"""Brand endpoints. The request body is the same BrandProfile the agents use, so a brand that the
API accepts is guaranteed to be usable by the pipeline (FastAPI validates it automatically)."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.api.deps import current_org, get_db
from buildsocial.api.schemas import BrandOut, BrandSummary
from buildsocial.db import brands as brand_ops
from buildsocial.db.models import Organization
from buildsocial.schemas import BrandProfile

router = APIRouter(prefix="/brands", tags=["brands"])


@router.post("", response_model=BrandOut, status_code=status.HTTP_201_CREATED)
async def create_brand(
    profile: BrandProfile, db: AsyncSession = Depends(get_db), org: Organization = Depends(current_org)
) -> BrandOut:
    try:
        brand = await brand_ops.create_brand(db, org, profile)
    except brand_ops.BrandNameTaken:
        raise HTTPException(status.HTTP_409_CONFLICT, f"a brand named '{profile.name}' already exists")
    return BrandOut.from_row(brand)


@router.get("", response_model=list[BrandSummary])
async def list_brands(db: AsyncSession = Depends(get_db), org: Organization = Depends(current_org)):
    return [BrandSummary.from_row(b) for b in await brand_ops.list_brands(db, org)]


@router.get("/{brand_id}", response_model=BrandOut)
async def get_brand(
    brand_id: uuid.UUID, db: AsyncSession = Depends(get_db), org: Organization = Depends(current_org)
) -> BrandOut:
    brand = await brand_ops.get_brand(db, org, brand_id)
    if brand is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand not found")
    return BrandOut.from_row(brand)


@router.put("/{brand_id}", response_model=BrandOut)
async def replace_brand(
    brand_id: uuid.UUID,
    profile: BrandProfile,
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(current_org),
) -> BrandOut:
    """Replace the whole profile (send the complete BrandProfile, not just changed fields)."""
    brand = await brand_ops.get_brand(db, org, brand_id)
    if brand is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand not found")
    try:
        brand = await brand_ops.update_brand(db, org, brand, profile)
    except brand_ops.BrandNameTaken:
        raise HTTPException(status.HTTP_409_CONFLICT, f"a brand named '{profile.name}' already exists")
    return BrandOut.from_row(brand)
