"""Brand operations, shared by the API and the CLI (so the logic lives in one place)."""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.db.models import Brand, Organization
from buildsocial.schemas import BrandProfile

# Until login exists, everything belongs to one organization (created on first use).
DEFAULT_ORG_NAME = "Default organization"


class BrandNameTaken(Exception):
    pass


async def default_org(db: AsyncSession) -> Organization:
    org = (await db.execute(select(Organization).order_by(Organization.created_at).limit(1))).scalar_one_or_none()
    if org is None:
        org = Organization(name=DEFAULT_ORG_NAME)
        db.add(org)
        await db.flush()
    return org


async def list_brands(db: AsyncSession, org: Organization) -> list[Brand]:
    result = await db.execute(select(Brand).where(Brand.organization_id == org.id).order_by(Brand.name))
    return list(result.scalars())


async def get_brand(db: AsyncSession, org: Organization, brand_id: uuid.UUID) -> Brand | None:
    # Always filter by organization: one customer must never reach another's brand by guessing an id.
    result = await db.execute(select(Brand).where(Brand.id == brand_id, Brand.organization_id == org.id))
    return result.scalar_one_or_none()


async def find_by_name(db: AsyncSession, org: Organization, name: str) -> Brand | None:
    result = await db.execute(select(Brand).where(Brand.organization_id == org.id, Brand.name == name))
    return result.scalar_one_or_none()


async def create_brand(db: AsyncSession, org: Organization, profile: BrandProfile) -> Brand:
    if await find_by_name(db, org, profile.name):
        raise BrandNameTaken(profile.name)
    brand = Brand(organization_id=org.id)
    _apply(brand, profile)
    db.add(brand)
    await db.flush()
    await db.refresh(brand)
    return brand


async def update_brand(db: AsyncSession, org: Organization, brand: Brand, profile: BrandProfile) -> Brand:
    other = await find_by_name(db, org, profile.name)
    if other is not None and other.id != brand.id:
        raise BrandNameTaken(profile.name)
    _apply(brand, profile)
    await db.flush()
    await db.refresh(brand)
    return brand


async def upsert_brand(db: AsyncSession, org: Organization, profile: BrandProfile) -> tuple[Brand, bool]:
    """Create the brand, or update the one with the same name. Returns (brand, created)."""
    existing = await find_by_name(db, org, profile.name)
    if existing is None:
        return await create_brand(db, org, profile), True
    return await update_brand(db, org, existing, profile), False


def _apply(brand: Brand, profile: BrandProfile) -> None:
    brand.name = profile.name
    brand.industry = profile.industry
    brand.market = profile.market
    brand.profile = profile.model_dump(mode="json")
