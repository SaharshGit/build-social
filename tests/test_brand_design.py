"""Brand design kit (colours, fonts, art direction) and the assets table for uploaded photos."""

import uuid
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from sqlalchemy import select

from buildsocial.db.models import Asset, AssetKind, Brand, Organization
from buildsocial.schemas import BrandDesign, BrandProfile, PostBrief, contrast_ratio
from tests.test_api_runs import create_brand

EXAMPLES = Path(__file__).parent.parent / "examples"


def design(**overrides) -> dict:
    d = {
        "colors": [{"name": "Cream", "hex": "#F3EADB", "role": "background"},
                   {"name": "Espresso", "hex": "#2E1D14", "role": "text"}],
        "fonts": {"heading": {"family": "Fraunces"}, "body": {"family": "DM Sans"}},
    }
    return d | overrides


def test_contrast_ratio_matches_wcag():
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0)
    assert contrast_ratio("#777777", "#777777") == pytest.approx(1.0)
    assert contrast_ratio("#767676", "#FFFFFF") == pytest.approx(4.54, abs=0.01)  # the classic AA grey


def test_brew_house_kit_loads_and_is_readable():
    brand = BrandProfile.model_validate(yaml.safe_load((EXAMPLES / "brew-house.yaml").read_text()))
    d = brand.design
    assert d is not None and d.fonts.heading.family == "Fraunces"
    assert contrast_ratio(d.color("text").hex, d.color("background").hex) >= 4.5


@pytest.mark.parametrize("colors, error", [
    ([{"name": "x", "hex": "#F3EADB", "role": "background"}], "at least one 'background' and one 'text'"),
    ([{"name": "x", "hex": "#F3EADB", "role": "background"}, {"name": "y", "hex": "#D9C9B0", "role": "text"}],
     "needs at least 4.5:1"),
    ([{"name": "x", "hex": "F3EADB", "role": "background"}], "should match pattern"),
])
def test_bad_kits_are_rejected(colors, error):
    with pytest.raises(ValidationError, match=error):
        BrandDesign.model_validate(design(colors=colors))


def test_design_and_photos_are_optional():
    profile = yaml.safe_load((EXAMPLES / "mamaearth.yaml").read_text())
    assert BrandProfile.model_validate(profile).design is None
    assert PostBrief(topic="x", platform="instagram").photo_ids == []


async def test_brand_api_keeps_the_design(client):
    brand_id = await create_brand(client)
    brand = (await client.get(f"/brands/{brand_id}")).json()
    assert brand["profile"]["design"]["fonts"]["body"]["family"] == "DM Sans"

    bad = yaml.safe_load((EXAMPLES / "brew-house.yaml").read_text())
    bad["name"] = "Low contrast cafe"
    bad["design"]["colors"][1]["hex"] = "#E8DCC8"  # text almost the colour of the background
    assert (await client.post("/brands", json=bad)).status_code == 422


async def test_asset_rows_default_to_the_brand_library(db_maker):
    async with db_maker() as db:
        org = Organization(name="o")
        db.add(org)
        await db.flush()
        brand = Brand(organization_id=org.id, name="b", industry="i", market="m", profile={})
        db.add(brand)
        await db.flush()
        asset = Asset(organization_id=org.id, brand_id=brand.id, kind=AssetKind.PRODUCT,
                      key=f"brands/{brand.id}/assets/{uuid.uuid4()}.jpg", content_type="image/jpeg",
                      size_bytes=1234, product="Masala Chai Cold Brew")
        db.add(asset)
        await db.commit()
        saved = (await db.execute(select(Asset))).scalar_one()
        assert saved.in_library is True and saved.description is None
