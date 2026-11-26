"""Uploading brand images: checked, cleaned (no metadata, upright), stored, served, edited, deleted."""

import uuid
from io import BytesIO

import pytest
import yaml
from PIL import Image

from buildsocial.api.deps import get_store
from buildsocial.harness.storage import MemoryStorage
from buildsocial.images import MAX_BYTES
from tests.test_api_runs import EXAMPLES, create_brand

GPS_TAG, ORIENTATION_TAG = 0x8825, 0x0112


@pytest.fixture
def store(client) -> MemoryStorage:
    store = MemoryStorage()
    client._transport.app.dependency_overrides[get_store] = lambda: store
    return store


def image_bytes(fmt="JPEG", size=(400, 300), orientation: int | None = None, gps=True) -> bytes:
    im = Image.new("RGB", size, (201, 119, 47))
    exif = Image.Exif()
    if gps:
        exif[GPS_TAG] = {1: "N", 2: (12.0, 58.0, 0.0)}  # where the photo was taken (Bengaluru)
    if orientation:
        exif[ORIENTATION_TAG] = orientation
    out = BytesIO()
    im.save(out, fmt, exif=exif.tobytes())
    return out.getvalue()


async def upload(client, brand_id, data, name="photo.jpg", **form):
    return await client.post(f"/brands/{brand_id}/assets", files={"file": (name, data)},
                             data={k: str(v).lower() if isinstance(v, bool) else v for k, v in form.items()})


@pytest.mark.parametrize("fmt, content_type", [("JPEG", "image/jpeg"), ("PNG", "image/png"), ("WEBP", "image/webp")])
async def test_upload_cleans_and_serves_the_image(client, store, fmt, content_type):
    brand_id = await create_brand(client)
    r = await upload(client, brand_id, image_bytes(fmt), name="IMG_2041.HEIC.jpg", kind="product",
                     product="masala chai cold brew")
    assert r.status_code == 201, r.text
    a = r.json()
    assert a["content_type"] == content_type  # from the contents, not the file name
    assert (a["width"], a["height"]) == (400, 300)
    assert a["product"] == "Masala Chai Cold Brew"  # the brand's own spelling
    assert a["kind"] == "product" and a["in_library"] is True and a["description"] is None
    assert a["original_name"] == "IMG_2041.HEIC.jpg"

    key = next(iter(store.files))
    assert key == f"brands/{brand_id}/assets/{a['id']}.{ {'JPEG': 'jpg', 'PNG': 'png', 'WEBP': 'webp'}[fmt] }"
    with Image.open(BytesIO(store.files[key])) as saved:
        assert GPS_TAG not in saved.getexif()  # the location is gone

    f = await client.get(a["url"])
    assert f.status_code == 200 and f.content == store.files[key]
    assert f.headers["content-type"] == content_type
    assert f.headers["x-content-type-options"] == "nosniff"


async def test_phone_rotation_is_applied(client, store):
    brand_id = await create_brand(client)
    # Orientation 6: the camera saved it sideways and asks viewers to rotate 90 degrees.
    a = (await upload(client, brand_id, image_bytes(size=(400, 300), orientation=6))).json()
    assert (a["width"], a["height"]) == (300, 400)


@pytest.mark.parametrize("data, name, error", [
    (b"just some text, not an image", "fake.png", "isn't a readable image"),
    (b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>', "logo.svg", "readable image"),
    (None, "tiny.png", "too small"),
    (None, "anim.gif", "use a PNG, JPEG or WebP"),
])
async def test_bad_uploads_are_rejected(client, store, data, name, error):
    brand_id = await create_brand(client)
    if name == "tiny.png":
        data = image_bytes("PNG", size=(120, 120), gps=False)
    if name == "anim.gif":
        out = BytesIO()
        Image.new("RGB", (300, 300)).save(out, "GIF")
        data = out.getvalue()
    r = await upload(client, brand_id, data, name=name)
    assert r.status_code == 422 and error in r.json()["detail"]
    assert store.files == {}  # nothing stored


async def test_too_large_and_unknown_product_and_brand(client, store):
    brand_id = await create_brand(client)
    assert (await upload(client, brand_id, b"\0" * (MAX_BYTES + 1))).status_code == 413
    r = await upload(client, brand_id, image_bytes(), product="Cold Filter Kaapi")
    assert r.status_code == 422 and "isn't one of this brand's products" in r.json()["detail"]
    assert (await upload(client, uuid.uuid4(), image_bytes())).status_code == 404
    assert (await client.get(f"/assets/{uuid.uuid4()}/file")).status_code == 404
    assert store.files == {}


async def test_list_edit_and_delete(client, store):
    brand_id = await create_brand(client)
    lib = (await upload(client, brand_id, image_bytes(), kind="product")).json()
    one_post = (await upload(client, brand_id, image_bytes(), in_library=False)).json()

    everything = (await client.get(f"/brands/{brand_id}/assets")).json()
    assert {a["id"] for a in everything} == {lib["id"], one_post["id"]}
    library = (await client.get(f"/brands/{brand_id}/assets", params={"in_library": True})).json()
    assert [a["id"] for a in library] == [lib["id"]]

    r = await client.patch(f"/assets/{one_post['id']}", json={
        "in_library": True, "description": "  A glass of cold brew on a wooden table.  ",
        "product": "Masala Chai Cold Brew", "kind": "lifestyle"})
    a = r.json()
    assert a["in_library"] and a["description"] == "A glass of cold brew on a wooden table."
    assert a["kind"] == "lifestyle" and a["product"] == "Masala Chai Cold Brew"
    a = (await client.patch(f"/assets/{one_post['id']}", json={"product": None})).json()
    assert a["product"] is None and a["kind"] == "lifestyle"  # only the sent field changed

    assert (await client.delete(f"/assets/{lib['id']}")).status_code == 204
    assert (await client.get(f"/assets/{lib['id']}")).status_code == 404
    assert len(store.files) == 1


async def test_the_logo_cant_be_deleted_while_in_use(client, store):
    brand_id = await create_brand(client)
    logo = (await upload(client, brand_id, image_bytes("PNG"), name="logo.png", kind="logo")).json()
    profile = yaml.safe_load((EXAMPLES / "brew-house.yaml").read_text())
    profile["design"]["logo_asset_id"] = logo["id"]
    assert (await client.put(f"/brands/{brand_id}", json=profile)).status_code == 200

    r = await client.delete(f"/assets/{logo['id']}")
    assert r.status_code == 409 and "logo" in r.json()["detail"]
    assert len(store.files) == 1


async def test_logo_must_be_an_image_of_the_same_brand(client, store):
    brand_id = await create_brand(client)
    profile = yaml.safe_load((EXAMPLES / "brew-house.yaml").read_text())
    other_id = (await client.post("/brands", json=profile | {"name": "Other cafe"})).json()["id"]
    foreign = (await upload(client, other_id, image_bytes("PNG"), kind="logo")).json()

    for bad in [foreign["id"], str(uuid.uuid4()), "not-a-uuid"]:
        profile["design"]["logo_asset_id"] = bad
        r = await client.put(f"/brands/{brand_id}", json=profile)
        assert r.status_code == 422 and "uploaded to this brand" in r.json()["detail"]
    assert (await client.post("/brands", json=profile | {"name": "Brand new cafe"})).status_code == 422
