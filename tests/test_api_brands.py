"""API tests: health and brand endpoints, against a throwaway database (see conftest.py)."""

import uuid
from pathlib import Path

import yaml

from buildsocial.db import brands as brand_ops
from buildsocial.schemas import BrandProfile

EXAMPLES = Path(__file__).parent.parent / "examples"


def profile(name: str = "mamaearth") -> dict:
    return yaml.safe_load((EXAMPLES / f"{name}.yaml").read_text())


async def test_health(client):
    r = await client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok", "database": "ok"}


async def test_create_then_get_brand(client):
    r = await client.post("/brands", json=profile())
    assert r.status_code == 201
    created = r.json()
    assert created["profile"]["name"] == "Mamaearth"
    assert created["profile"]["products"][0]["name"] == "Onion Hair Oil"  # nested data survives the round trip

    r = await client.get(f"/brands/{created['id']}")
    assert r.status_code == 200 and r.json()["profile"] == created["profile"]


async def test_list_brands_sorted_with_summaries(client):
    for name in ("zerodha", "brew-house", "mamaearth"):
        await client.post("/brands", json=profile(name))
    r = await client.get("/brands")
    assert [b["name"] for b in r.json()] == ["Brew House", "Mamaearth", "Zerodha"]
    assert r.json()[1]["products"] == 1 and r.json()[1]["market"] == "India"


async def test_duplicate_name_is_409(client):
    await client.post("/brands", json=profile())
    r = await client.post("/brands", json=profile())
    assert r.status_code == 409 and "already exists" in r.json()["detail"]


async def test_invalid_brand_is_rejected_with_details(client):
    bad = profile()
    del bad["industry"]  # required: shared market research is keyed on it
    r = await client.post("/brands", json=bad)
    assert r.status_code == 422
    assert any(e["loc"][-1] == "industry" for e in r.json()["detail"])


async def test_unknown_brand_is_404(client):
    r = await client.get(f"/brands/{uuid.uuid4()}")
    assert r.status_code == 404
    assert (await client.get("/brands/not-a-uuid")).status_code == 422


async def test_replace_brand(client):
    brand_id = (await client.post("/brands", json=profile())).json()["id"]
    changed = profile() | {"voice": "Calm and clinical."}
    r = await client.put(f"/brands/{brand_id}", json=changed)
    assert r.status_code == 200 and r.json()["profile"]["voice"] == "Calm and clinical."
    assert (await client.get(f"/brands/{brand_id}")).json()["profile"]["voice"] == "Calm and clinical."


async def test_rename_onto_existing_name_is_409(client):
    await client.post("/brands", json=profile("mamaearth"))
    z = (await client.post("/brands", json=profile("zerodha"))).json()["id"]
    r = await client.put(f"/brands/{z}", json=profile("zerodha") | {"name": "Mamaearth"})
    assert r.status_code == 409


async def test_upsert_creates_then_updates(db_maker):
    """What `buildsocial import-brands` uses: safe to run twice."""
    p = BrandProfile.model_validate(profile())
    async with db_maker() as db:
        org = await brand_ops.default_org(db)
        _, created = await brand_ops.upsert_brand(db, org, p)
        brand, created_again = await brand_ops.upsert_brand(db, org, p.model_copy(update={"voice": "New"}))
        assert created and not created_again and brand.profile["voice"] == "New"
        assert len(await brand_ops.list_brands(db, org)) == 1
