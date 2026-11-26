"""Reference board: the brand's style is read from 3-15 reference images, kept apart from the profile."""

import json
from io import BytesIO

import pytest
import yaml
from PIL import Image

from buildsocial.agents.brand_style import BrandStyle, BrandStyleAgent, BrandStyleInput
from buildsocial.api.deps import get_model_deps
from buildsocial.brand_style import kit_mismatch
from buildsocial.harness.llm.types import ImageInput
from buildsocial.images import for_model
from buildsocial.photo_reading import ModelDeps
from tests.test_api_assets import image_bytes, store, upload  # noqa: F401 (store is a fixture)
from tests.test_api_runs import EXAMPLES, INR, create_brand
from tests.test_photo_reader import READING, RecordingLLM


def style_json(n: int, **change) -> str:
    style = {
        "per_image": [f"image {i + 1}: iced drink on wood, warm light" for i in range(n)],
        "palette": ["#F3EADB", "#C9772F", "#2E1D14"],
        "color_use": "cream backgrounds with one chai-orange accent",
        "lighting": "soft warm daylight from a window",
        "composition": "close crops, product off-centre, empty space at the top",
        "textures_props": ["wooden table", "steel tumbler"],
        "text_on_image": "short lowercase serif headline, top left",
        "motifs": ["condensation on cold glasses"],
        "mood": ["cosy", "warm", "playful"],
        "avoid": ["neon colours", "busy collages"],
        "summary": "Warm, close, uncluttered cafe shots on cream with one orange accent.",
    }
    return json.dumps(style | change)


@pytest.fixture
def models(client, store):  # noqa: F811
    """The scripted model: photo readings for uploads, then style readings."""
    llm = RecordingLLM([])
    client._transport.app.dependency_overrides[get_model_deps] = lambda: ModelDeps(
        client_for=lambda name: llm, pricing_for=lambda n: INR)
    return llm


async def add_references(client, models, brand_id, n, first: dict | None = None):
    """Upload n references; `first` changes the first one's photo reading (e.g. other_brands)."""
    for i in range(n):
        models.replies.append(json.dumps(READING | (first or {} if i == 0 else {})))
        r = await upload(client, brand_id, image_bytes(size=(1600, 1200)), name=f"ref{i}.jpg", kind="reference")
        assert r.status_code == 201


# --- agent ---------------------------------------------------------------------------------


def test_agent_checks_one_line_per_image_and_counts():
    imgs = [ImageInput(mime_type="image/jpeg", data=b"x")] * 3
    inp = BrandStyleInput(brand_name="Brew House", images=imgs)
    agent = BrandStyleAgent()
    assert agent.check(BrandStyle.model_validate_json(style_json(3)), inp) == []
    errors = agent.check(BrandStyle.model_validate_json(style_json(2, mood=["cosy"], palette=["#FFFFFF"])), inp)
    assert any("one line per image (3)" in e for e in errors)
    assert any("palette" in e for e in errors) and any("mood" in e for e in errors)
    assert agent.build_images(inp) == imgs and "numbered 1 to 3" in agent.build_user_message(inp)


def test_model_copies_are_small_jpegs():
    big = image_bytes("PNG", size=(4000, 3000), gps=False)
    data, mime = for_model(big, max_side=1024)
    with Image.open(BytesIO(data)) as im:
        assert mime == "image/jpeg" and im.format == "JPEG" and max(im.size) == 1024


def test_kit_mismatch_flags_only_clearly_different_colours():
    kit = ["#F3EADB", "#2E1D14", "#C9772F"]
    assert kit_mismatch(["#F1E8D8", "#C67430"], kit) == []  # nearly the kit colours
    assert kit_mismatch(["#00A3FF", "#F3EADB"], kit) == ["#00A3FF"]  # bright blue: not in the kit
    assert kit_mismatch(["#00A3FF"], []) == []  # no kit, nothing to compare


# --- API -----------------------------------------------------------------------------------


async def test_read_style_from_references(client, store, models):  # noqa: F811
    brand_id = await create_brand(client)
    await add_references(client, models, brand_id, 3)
    models.replies.append(json.dumps(READING))
    await upload(client, brand_id, image_bytes(), kind="product")  # not a reference: ignored
    models.replies.append(style_json(3))

    s = (await client.post(f"/brands/{brand_id}/style/read")).json()
    assert s["reading"]["lighting"] == "soft warm daylight from a window"
    assert len(s["asset_ids"]) == 3 and s["references_usable"] == 3 and s["stale"] is False
    assert s["kit_mismatch"] == [] and s["model_cost"] > 0

    sent = models.sent[-1][-1]
    assert len(sent.images) == 3 and all(i.resolution == "medium" for i in sent.images)
    with Image.open(BytesIO(sent.images[0].data)) as im:
        assert max(im.size) == 1024  # downscaled from 1600

    # Kept outside the profile: replacing the profile doesn't wipe it.
    profile = yaml.safe_load((EXAMPLES / "brew-house.yaml").read_text())
    assert (await client.put(f"/brands/{brand_id}", json=profile)).status_code == 200
    assert (await client.get(f"/brands/{brand_id}/style")).json()["reading"] is not None
    # A new reference makes it stale.
    await add_references(client, models, brand_id, 1)
    assert (await client.get(f"/brands/{brand_id}/style")).json()["stale"] is True


async def test_needs_three_usable_references_and_skips_other_brands(client, store, models):  # noqa: F811
    brand_id = await create_brand(client)
    await add_references(client, models, brand_id, 3, first={"other_brands": ["Starbucks"]})
    r = await client.post(f"/brands/{brand_id}/style/read")
    assert r.status_code == 422 and "have 2 usable" in r.json()["detail"]
    s = (await client.get(f"/brands/{brand_id}/style")).json()
    assert s["references_usable"] == 2 and s["references_excluded"][0]["reason"] == "shows another brand: Starbucks"


async def test_the_brands_own_logo_is_fine(client, store, models):  # noqa: F811
    brand_id = await create_brand(client)
    await add_references(client, models, brand_id, 3, first={"issues": ["Brew House logo in the corner"],
                                                             "visible_text": ["Brew House"]})
    assert (await client.get(f"/brands/{brand_id}/style")).json()["references_usable"] == 3


async def test_unchecked_references_arent_trusted(client, store, models):  # noqa: F811
    from buildsocial.harness.llm.base import LLMError
    brand_id = await create_brand(client)
    await add_references(client, models, brand_id, 3)
    models.replies.append(LLMError("model down"))  # this one's reading fails
    await upload(client, brand_id, image_bytes(), kind="reference")
    s = (await client.get(f"/brands/{brand_id}/style")).json()
    assert s["references_usable"] == 3 and "not checked yet" in s["references_excluded"][0]["reason"]


async def test_a_missing_file_is_skipped(client, store, models):  # noqa: F811
    brand_id = await create_brand(client)
    await add_references(client, models, brand_id, 4)
    store.files.pop(next(iter(store.files)))  # one file vanished from storage
    models.replies.append(style_json(3))
    s = (await client.post(f"/brands/{brand_id}/style/read")).json()
    assert len(s["asset_ids"]) == 3 and s["excluded"][0]["reason"] == "the image file is missing"


async def test_a_persons_edit_is_protected(client, store, models):  # noqa: F811
    brand_id = await create_brand(client)
    await add_references(client, models, brand_id, 3)
    models.replies.append(style_json(3))
    await client.post(f"/brands/{brand_id}/style/read")

    r = await client.patch(f"/brands/{brand_id}/style", json={"lighting": "bright midday sun, hard shadows"})
    assert r.json()["edited"] is True and r.json()["reading"]["mood"] == ["cosy", "warm", "playful"]
    assert (await client.patch(f"/brands/{brand_id}/style", json={"palette": ["red"]})).status_code == 422

    assert (await client.post(f"/brands/{brand_id}/style/read")).status_code == 409
    models.replies.append(style_json(3))
    replaced = (await client.post(f"/brands/{brand_id}/style/read", params={"replace": True})).json()
    assert replaced["edited"] is False and replaced["reading"]["lighting"] == "soft warm daylight from a window"


async def test_edit_needs_a_style_first(client, store, models):  # noqa: F811
    brand_id = await create_brand(client)
    r = await client.patch(f"/brands/{brand_id}/style", json={"lighting": "x"})
    assert r.status_code == 422 and "read it first" in r.json()["detail"]
