"""A brand's reference board and the visual style read from it.

    GET   /brands/{brand_id}/style        the saved style, whether it's stale, usable references
    POST  /brands/{brand_id}/style/read   read the style from 3-15 reference images (about INR 0.2)
    PATCH /brands/{brand_id}/style        a person's correction (protected from later re-reads)

Reference images are ordinary uploads with kind "reference" (POST /brands/{id}/assets).
"""

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, create_model
from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial import brand_style as style_ops
from buildsocial.agents.brand_style import BrandStyle
from buildsocial.api.deps import current_org, get_db, get_model_deps, get_store
from buildsocial.db import brands as brand_ops
from buildsocial.db.models import Brand, Organization
from buildsocial.harness.agent import AgentError
from buildsocial.harness.llm.base import LLMError
from buildsocial.harness.storage import Storage
from buildsocial.photo_reading import ModelDeps

router = APIRouter(prefix="/brands/{brand_id}/style", tags=["style"])


class StyleOut(BaseModel):
    reading: BrandStyle | None = None
    asset_ids: list[str] = []
    excluded: list[dict[str, Any]] = []  # references the last read left out, and why
    kit_mismatch: list[str] = []  # recurring colours far from every kit colour
    edited: bool = False
    at: str | None = None
    model_cost: float | None = None
    currency: str | None = None
    references_usable: int = 0  # right now
    references_excluded: list[dict[str, Any]] = []  # right now
    stale: bool = False  # the usable references changed since the last read


# Every BrandStyle field, optional: send only what you want to change.
BrandStyleUpdate = create_model(
    "BrandStyleUpdate", **{name: (f.annotation | None, None) for name, f in BrandStyle.model_fields.items()})


async def _brand(db: AsyncSession, org: Organization, brand_id: uuid.UUID) -> Brand:
    brand = await brand_ops.get_brand(db, org, brand_id)
    if brand is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand not found")
    return brand


async def _out(db: AsyncSession, org: Organization, brand: Brand) -> StyleOut:
    view = await style_ops.style_view(db, org, brand)
    return StyleOut(**(view["style"] or {}), references_usable=view["references_usable"],
                    references_excluded=view["references_excluded"], stale=view["stale"])


@router.get("", response_model=StyleOut)
async def get_style(brand_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                    org: Organization = Depends(current_org)) -> StyleOut:
    return await _out(db, org, await _brand(db, org, brand_id))


@router.post("/read", response_model=StyleOut)
async def read_style(brand_id: uuid.UUID, replace: bool = False, db: AsyncSession = Depends(get_db),
                     org: Organization = Depends(current_org), store: Storage = Depends(get_store),
                     models: ModelDeps = Depends(get_model_deps)) -> StyleOut:
    brand = await _brand(db, org, brand_id)
    try:
        await style_ops.read_style(db, store, org, brand, models, replace_edited=replace)
    except style_ops.NotEnoughReferences as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e))
    except style_ops.StyleEdited as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
    except (AgentError, LLMError) as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"the style model failed: {str(e)[:200]}")
    return await _out(db, org, brand)


@router.patch("", response_model=StyleOut)
async def edit_style(brand_id: uuid.UUID, change: BrandStyleUpdate, db: AsyncSession = Depends(get_db),  # type: ignore[valid-type]
                     org: Organization = Depends(current_org)) -> StyleOut:
    brand = await _brand(db, org, brand_id)
    try:
        style_ops.edit_style(brand, change.model_dump(exclude_unset=True))
    except ValueError as e:  # no style yet, or the change is invalid (pydantic's error is a ValueError)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e)[:300])
    await db.flush()
    return await _out(db, org, brand)
