"""Image posts through the API and the worker: queue -> text post -> image -> PNG, and 'try another layout'."""

import json

import pytest

from buildsocial.worker import process_one
from tests.test_api_assets import image_bytes, store, upload  # noqa: F401 (store is a fixture)
from tests.test_api_runs import BRIEF, DRAFT, INR, PASS, PLAN, SEARCH, STRUCTURE, create_brand, scripted, worker_deps
from tests.test_imaging import FakeRenderer, design, no_fonts, verdict
from tests.test_photo_reader import READING, RecordingLLM
from buildsocial.api.deps import get_model_deps
from buildsocial.imaging import ImageDeps
from buildsocial.photo_reading import ModelDeps

IMAGE_BRIEF = BRIEF | {"format": "single_image"}
IMAGE_DRAFT = json.dumps(json.loads(DRAFT) | {"image_text": {"headline": "Baarish wali shaam?", "subline": "Warm cup."}})


def image_deps(store) -> ImageDeps:
    return ImageDeps(store=store, renderer=lambda: FakeRenderer(), font_css=no_fonts)


async def make_image_post(client, db_maker, store, designs, visuals, brand_id=None):
    brand_id = brand_id or await create_brand(client)
    run = (await client.post(f"/brands/{brand_id}/runs", json=IMAGE_BRIEF)).json()
    client_for, queues = scripted(research=[SEARCH, STRUCTURE, SEARCH, STRUCTURE], strategy=[PLAN],
                                  content=[IMAGE_DRAFT], reviewer=[PASS], designer=designs, visual_check=visuals)
    deps = worker_deps(db_maker, client_for)
    deps.images = image_deps(store)
    await process_one(deps)
    run = (await client.get(f"/runs/{run['id']}")).json()
    return brand_id, run, deps


async def test_an_image_post_gets_its_image_and_the_steps_show_it(client, db_maker, store):  # noqa: F811
    design_json = design(layout="type_led", field_role="accent")
    visual = verdict("Baarish wali shaam?", "Warm cup.")
    brand_id, run, _ = await make_image_post(client, db_maker, store, [design_json], [visual])

    assert run["status"] == "done" and run["post_id"]
    assert [s["agent"] for s in run["steps"]][-3:] == ["design", "render", "visual_check"]
    post = (await client.get(f"/posts/{run['post_id']}")).json()
    img = post["image"]
    assert img["status"] == "ok" and img["layout"] == "type_led" and img["version"] == 1
    assert img["url"] == f"/posts/{run['post_id']}/image?v=1" and img["visual"]["read"]["headline"] == "Baarish wali shaam?"

    png = await client.get(img["url"])
    assert png.status_code == 200 and png.headers["content-type"] == "image/png" and png.content[:4] == b"\x89PNG"
    assert png.headers["x-content-type-options"] == "nosniff"
    assert run["cost"]["INR"] > 0  # design + visual check were counted


async def test_text_posts_have_no_image(client, db_maker, store):  # noqa: F811
    brand_id = await create_brand(client)
    run = (await client.post(f"/brands/{brand_id}/runs", json=BRIEF)).json()
    client_for, _ = scripted(research=[SEARCH, STRUCTURE, SEARCH, STRUCTURE], strategy=[PLAN], content=[DRAFT], reviewer=[PASS])
    await process_one(worker_deps(db_maker, client_for))
    post_id = (await client.get(f"/runs/{run['id']}")).json()["post_id"]
    assert (await client.get(f"/posts/{post_id}")).json()["image"] is None
    assert (await client.get(f"/posts/{post_id}/image")).status_code == 404
    assert (await client.post(f"/posts/{post_id}/image")).status_code == 409  # only image posts have an image


async def test_an_image_failure_never_loses_the_post(client, db_maker, store):  # noqa: F811
    # The brand has no look & feel: the text post is saved, the image error is recorded on it.
    profile = (await client.get(f"/brands/{await create_brand(client)}")).json()["profile"]
    profile["name"], profile["design"] = "Plain cafe", None
    plain_id = (await client.post("/brands", json=profile)).json()["id"]
    _, run, _ = await make_image_post(client, db_maker, store, [], [], brand_id=plain_id)
    assert run["status"] == "done"
    post = (await client.get(f"/posts/{run['post_id']}")).json()
    assert "no look & feel yet" in post["image"]["error"] and post["image"]["url"] == ""
    assert post["full_text"]  # the caption is there


async def test_try_another_layout_queues_a_run_and_the_worker_changes_the_image(client, db_maker, store):  # noqa: F811
    # A brand photo exists, so a second layout is possible.
    brand_id = await create_brand(client)
    reading = RecordingLLM([json.dumps(READING)])
    client._transport.app.dependency_overrides[get_model_deps] = lambda: ModelDeps(
        client_for=lambda name: reading, pricing_for=lambda n: INR)
    photo = (await upload(client, brand_id, image_bytes(), kind="product", product="Masala Chai Cold Brew")).json()

    visual = verdict("Baarish wali shaam?", "Warm cup.")
    _, run, deps = await make_image_post(client, db_maker, store, [design(layout="type_led", field_role="accent")],
                                         [visual], brand_id=brand_id)
    post_id = run["post_id"]
    assert (await client.get(f"/posts/{post_id}")).json()["image"]["layout"] == "type_led"

    again = await client.post(f"/posts/{post_id}/image")
    assert again.status_code == 202 and again.json()["trigger"] == "form"
    assert (await client.post(f"/posts/{post_id}/image")).status_code == 409  # already being changed

    client_for, _ = scripted(designer=[design(layout="framed", photo_id=photo["id"])], visual_check=[visual])
    deps.client_for = client_for
    await process_one(deps)
    done = (await client.get(f"/runs/{again.json()['id']}")).json()
    assert done["status"] == "done"
    img = (await client.get(f"/posts/{post_id}")).json()["image"]
    assert (img["layout"], img["version"]) == ("framed", 2)
    assert (await client.get(img["url"])).status_code == 200
