"""Asset operations: uploaded images of a brand. The file goes to storage, the row to the database.

Everything is filtered by organization, like brands: one customer must never reach another's images.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.db.models import Asset, AssetKind, Brand, Organization
from buildsocial.harness.storage import Storage
from buildsocial.images import clean_image
from buildsocial.schemas import BrandProfile


class UnknownProduct(ValueError):
    pass


class AssetInUse(Exception):
    pass


def asset_key(brand_id: uuid.UUID, asset_id: uuid.UUID, ext: str) -> str:
    return f"brands/{brand_id}/assets/{asset_id}.{ext}"


def check_product(brand: Brand, product: str | None) -> str | None:
    """The product name as the brand writes it, or UnknownProduct. Matching ignores case."""
    if not product or not product.strip():
        return None
    names = {p.name.lower(): p.name for p in BrandProfile.model_validate(brand.profile).products}
    if product.strip().lower() not in names:
        raise UnknownProduct(f"'{product}' isn't one of this brand's products")
    return names[product.strip().lower()]


async def create_asset(db: AsyncSession, store: Storage, brand: Brand, data: bytes, *, kind: AssetKind,
                       original_name: str | None = None, product: str | None = None,
                       in_library: bool = True) -> Asset:
    """Check and clean the image (raises images.BadImage), store the file, then save the row."""
    product = check_product(brand, product)
    image = clean_image(data)
    asset = Asset(id=uuid.uuid4(), organization_id=brand.organization_id, brand_id=brand.id, kind=kind,
                  content_type=image.content_type, size_bytes=len(image.data), width=image.width,
                  height=image.height, original_name=(original_name or "")[:255] or None, product=product,
                  in_library=in_library)
    asset.key = asset_key(brand.id, asset.id, image.ext)
    store.write_bytes(asset.key, image.data, image.content_type)
    try:
        db.add(asset)
        await db.flush()
    except Exception:
        store.delete(asset.key)  # don't leave a file without a row
        raise
    return asset


async def list_assets(db: AsyncSession, org: Organization, brand_id: uuid.UUID,
                      in_library: bool | None = None) -> list[Asset]:
    q = select(Asset).where(Asset.organization_id == org.id, Asset.brand_id == brand_id)
    if in_library is not None:
        q = q.where(Asset.in_library == in_library)
    return list((await db.execute(q.order_by(Asset.created_at.desc()))).scalars())


async def get_asset(db: AsyncSession, org: Organization, asset_id: uuid.UUID) -> Asset | None:
    result = await db.execute(select(Asset).where(Asset.id == asset_id, Asset.organization_id == org.id))
    return result.scalar_one_or_none()


async def delete_asset(db: AsyncSession, store: Storage, brand: Brand, asset: Asset) -> None:
    design = BrandProfile.model_validate(brand.profile).design
    if design and design.logo_asset_id == str(asset.id):
        raise AssetInUse("this image is the brand's logo; choose another logo first")
    await db.delete(asset)
    await db.flush()
    store.delete(asset.key)
