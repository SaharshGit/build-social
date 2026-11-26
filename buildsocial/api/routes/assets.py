"""Uploaded images of a brand: logo, product photos, photos for one post.

    POST   /brands/{brand_id}/assets   upload (multipart form: file, kind, product, in_library)
    GET    /brands/{brand_id}/assets   list (?in_library=true for the library only)
    GET    /assets/{asset_id}          details
    GET    /assets/{asset_id}/file     the image itself
    PATCH  /assets/{asset_id}          change kind / product / description / in_library
    DELETE /assets/{asset_id}
"""

import uuid

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.api.deps import current_org, get_db, get_store
from buildsocial.api.schemas import AssetOut, AssetUpdate
from buildsocial.db import assets as asset_ops
from buildsocial.db import brands as brand_ops
from buildsocial.db.models import Asset, AssetKind, Brand, Organization
from buildsocial.harness.storage import Storage
from buildsocial.images import MAX_BYTES, BadImage

router = APIRouter(tags=["assets"])


async def _brand(db: AsyncSession, org: Organization, brand_id: uuid.UUID) -> Brand:
    brand = await brand_ops.get_brand(db, org, brand_id)
    if brand is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand not found")
    return brand


async def _asset(db: AsyncSession, org: Organization, asset_id: uuid.UUID) -> Asset:
    asset = await asset_ops.get_asset(db, org, asset_id)
    if asset is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "image not found")
    return asset


@router.post("/brands/{brand_id}/assets", response_model=AssetOut, status_code=status.HTTP_201_CREATED)
async def upload_asset(
    brand_id: uuid.UUID,
    file: UploadFile = File(description="PNG, JPEG or WebP, up to 10 MB"),
    kind: AssetKind = Form(AssetKind.OTHER),
    product: str | None = Form(None, description="The brand product it shows, by name"),
    in_library: bool = Form(True, description="false: a photo for one post only"),
    db: AsyncSession = Depends(get_db),
    org: Organization = Depends(current_org),
    store: Storage = Depends(get_store),
) -> AssetOut:
    brand = await _brand(db, org, brand_id)
    data = await file.read(MAX_BYTES + 1)  # never read more than the limit into memory
    if len(data) > MAX_BYTES:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, f"the image is larger than {MAX_BYTES // 2**20} MB")
    try:
        asset = await asset_ops.create_asset(db, store, brand, data, kind=kind, original_name=file.filename,
                                             product=product, in_library=in_library)
    except (BadImage, asset_ops.UnknownProduct) as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e))
    return AssetOut.from_row(asset)


@router.get("/brands/{brand_id}/assets", response_model=list[AssetOut])
async def list_assets(brand_id: uuid.UUID, in_library: bool | None = None, db: AsyncSession = Depends(get_db),
                      org: Organization = Depends(current_org)) -> list[AssetOut]:
    await _brand(db, org, brand_id)
    return [AssetOut.from_row(a) for a in await asset_ops.list_assets(db, org, brand_id, in_library)]


@router.get("/assets/{asset_id}", response_model=AssetOut)
async def get_asset(asset_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                    org: Organization = Depends(current_org)) -> AssetOut:
    return AssetOut.from_row(await _asset(db, org, asset_id))


@router.get("/assets/{asset_id}/file", response_class=Response,
            responses={200: {"content": {"image/png": {}, "image/jpeg": {}, "image/webp": {}}}})
async def get_asset_file(asset_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                         org: Organization = Depends(current_org), store: Storage = Depends(get_store)) -> Response:
    asset = await _asset(db, org, asset_id)
    try:
        data = store.read_bytes(asset.key)
    except FileNotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "the image file is missing")
    # nosniff: browsers must treat it as the image type we checked, never guess another type.
    # private: the image belongs to one organization, so shared caches must not keep it.
    return Response(data, media_type=asset.content_type,
                    headers={"X-Content-Type-Options": "nosniff", "Cache-Control": "private, max-age=3600"})


@router.patch("/assets/{asset_id}", response_model=AssetOut)
async def update_asset(asset_id: uuid.UUID, change: AssetUpdate, db: AsyncSession = Depends(get_db),
                       org: Organization = Depends(current_org)) -> AssetOut:
    asset = await _asset(db, org, asset_id)
    sent = change.model_fields_set
    if "product" in sent:
        brand = await _brand(db, org, asset.brand_id)
        try:
            asset.product = asset_ops.check_product(brand, change.product)
        except asset_ops.UnknownProduct as e:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(e))
    if "kind" in sent and change.kind is not None:
        asset.kind = change.kind
    if "description" in sent:
        asset.description = (change.description or "").strip() or None
    if "in_library" in sent and change.in_library is not None:
        asset.in_library = change.in_library
    await db.flush()
    return AssetOut.from_row(asset)


@router.delete("/assets/{asset_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_asset(asset_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                       org: Organization = Depends(current_org), store: Storage = Depends(get_store)) -> None:
    asset = await _asset(db, org, asset_id)
    brand = await _brand(db, org, asset.brand_id)
    try:
        await asset_ops.delete_asset(db, store, brand, asset)
    except asset_ops.AssetInUse as e:
        raise HTTPException(status.HTTP_409_CONFLICT, str(e))
