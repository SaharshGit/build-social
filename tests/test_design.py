"""The design step: what the design agent may choose, which photos are candidates, and the page built from it."""

import json
import uuid

import pytest

from buildsocial.agents.designer import DesignChoice, DesignerAgent, DesignInput, PhotoOption
from buildsocial.db.models import Asset, AssetKind, Brand, Organization
from buildsocial.design import candidates, design_input, fallback_choice, page_spec
from buildsocial.harness.storage import MemoryStorage
from buildsocial.schemas import ImageText, PostBrief
from tests.test_photo_reader import RecordingLLM
from tests.test_render import BREW, photo
from tests.test_render_checks import names  # noqa: F401  (shared helpers)
from tests.test_content_agent import make_draft

BRIEF = PostBrief(topic="new Masala Chai Cold Brew", platform="instagram", format="single_image")


def option(id="p1", text_space=("top",), **change) -> PhotoOption:
    return PhotoOption(id=id, description="A glass of iced tea on a plain grey background.",
                       text_space=list(text_space), background="plain", **change)


def inp(photos=(), subline="Masala Chai Cold Brew, new on the menu") -> DesignInput:
    return DesignInput(brand=BREW, brief=BRIEF, headline="Cold chai weather, anyone?", subline=subline,
                       photos=list(photos))


def choice(**change) -> DesignChoice:
    base = dict(directions=["a", "b", "c"], rationale="r", layout="photo", photo_id="p1", text_area="top",
                focus="center", field_role=None, scrim=False)
    return DesignChoice(**(base | change))


def errors(c: DesignChoice, i: DesignInput) -> list[str]:
    return DesignerAgent().check(c, i)


# --- what the agent may choose -----------------------------------------------------------


def test_a_valid_photo_choice_passes():
    assert errors(choice(), inp([option()])) == []


def test_photo_layout_needs_a_calm_area_to_put_text_in():
    assert any("no calm area" in e for e in errors(choice(), inp([option(text_space=())])))
    assert any("no calm area" in e for e in errors(choice(), inp([option(text_space=("left", "center"))])))
    assert any("must be one of ['bottom']" in e for e in errors(choice(text_area="top"), inp([option(text_space=("bottom",))])))
    assert errors(choice(layout="split", text_area=None), inp([option(text_space=())])) == []  # text beside the photo


def test_the_photo_must_be_one_that_exists():
    assert any("photo_id: must be one of" in e for e in errors(choice(photo_id="made-up"), inp([option()])))
    assert any("no photos are available" in e for e in errors(choice(), inp([])))


def test_an_attached_photo_must_be_used():
    attached = inp([option("p1"), option("p2", attached=True)])
    assert any("use the photo the person attached (p2)" in e for e in errors(choice(photo_id="p1"), attached))
    assert errors(choice(photo_id="p2"), attached) == []
    assert any("must use it" in e for e in errors(choice(layout="type_led", photo_id=None, text_area=None, field_role="accent"), attached))


def test_type_led_needs_a_colour_text_can_be_read_on():
    ok = choice(layout="type_led", photo_id=None, text_area=None, field_role="accent")  # espresso on chai, 4.7:1
    assert errors(ok, inp()) == []
    assert any("field_role" in e for e in errors(ok.model_copy(update={"field_role": "text"}), inp()))
    assert any("field_role" in e for e in errors(ok.model_copy(update={"field_role": None}), inp()))
    # Cardamom: neither kit text colour reaches 4.5:1 on it, so small text couldn't be read.
    assert any("no kit text colour reads" in e for e in errors(ok.model_copy(update={"field_role": "secondary"}), inp()))
    assert errors(ok.model_copy(update={"field_role": "secondary"}), inp(subline=None)) == []  # headline only: 3:1 is enough


def test_scrim_is_only_for_the_photo_layout():
    assert any("scrim" in e for e in errors(choice(layout="split", text_area=None, scrim=True), inp([option()])))


def test_the_agent_sees_the_kit_photos_and_fixed_words_but_no_free_text_field():
    agent = DesignerAgent()
    msg = agent.build_user_message(inp([option("p1", attached=True, product="Masala Chai Cold Brew")]))
    assert "- accent: Chai #C9772F" in msg and "Headline: Cold chai weather, anyone?" in msg
    assert "id p1 [ATTACHED BY THE PERSON: must be used]" in msg and "calm areas for text: top" in msg
    prompt = agent.build_system_prompt(inp())
    assert "## Design: layout" in prompt and "# Canvas: instagram single_image" in prompt and "x 98-982" in prompt
    assert set(DesignChoice.model_fields) == {"directions", "rationale", "layout", "photo_id", "text_area", "focus",
                                              "field_role", "scrim"}  # no place to write words


async def test_the_agent_retries_with_the_reasons():
    bad = choice(text_area="bottom").model_dump_json()  # the photo's calm area is the top
    good = choice().model_dump_json()
    llm = RecordingLLM([bad, good])
    result = await DesignerAgent().run(inp([option()]), llm)
    assert result.output.text_area == "top" and result.attempts == 2
    assert "must be one of ['top']" in (llm.sent[1][-1].content or "")


# --- the fallback never needs the agent ---------------------------------------------------


def test_fallback_designs_pass_the_same_checks():
    with_photo = inp([option("p1"), option("p2", attached=True)])
    c = fallback_choice(with_photo)
    assert (c.layout, c.photo_id) == ("split", "p2") and errors(c, with_photo) == []
    plain = fallback_choice(inp())
    assert (plain.layout, plain.field_role) == ("type_led", "accent") and errors(plain, inp()) == []


# --- which photos are candidates ----------------------------------------------------------

READ_OK = {"other_brands": [], "issues": [], "text_space": ["top"], "background": "plain"}


async def seed(db_maker, assets: list[dict]):
    async with db_maker() as db:
        org = Organization(name="o")
        db.add(org)
        await db.flush()
        brand = Brand(organization_id=org.id, name="Brew House", industry="cafe", market="Bengaluru",
                      profile=BREW.model_dump(mode="json"))
        db.add(brand)
        await db.flush()
        rows = []
        for a in assets:
            row = Asset(organization_id=org.id, brand_id=brand.id, kind=a.get("kind", AssetKind.PRODUCT),
                        key=f"brands/{brand.id}/assets/{uuid.uuid4()}.jpg", content_type="image/jpeg", size_bytes=1,
                        product=a.get("product"), in_library=a.get("in_library", True),
                        analysis=a.get("analysis", READ_OK), description=a.get("description"))
            db.add(row)
            rows.append(row)
        await db.commit()
        return org, brand, rows


async def test_candidates_put_attached_photos_first_and_skip_unusable_ones(db_maker):
    org, brand, rows = await seed(db_maker, [
        {"product": "Masala Chai Cold Brew"},                                    # 0: matches the topic
        {"product": None},                                                       # 1: some other product photo
        {"kind": AssetKind.REFERENCE},                                           # 2: reference, not a library photo
        {"analysis": READ_OK | {"issues": ["blurry"]}},                          # 3: has problems
        {"analysis": {}},                                                        # 4: never read
        {"analysis": READ_OK | {"other_brands": ["Starbucks"]}},                 # 5: another brand
        {"kind": AssetKind.OTHER, "in_library": False},                          # 6: attached to this post
        {"kind": AssetKind.OTHER, "in_library": False, "analysis": READ_OK | {"issues": ["faces"]}},  # 7: attached, flagged
        {"kind": AssetKind.OTHER, "in_library": False, "analysis": READ_OK | {"other_brands": ["Nike"]}},  # 8: attached, other brand
    ])
    brief = BRIEF.model_copy(update={"photo_ids": [str(rows[6].id), str(rows[8].id), str(rows[7].id), str(uuid.uuid4())]})
    async with db_maker() as db:
        c = await candidates(db, org, brand, brief)
    ids = [str(a.id) for a in c.assets]
    assert ids == [str(rows[6].id), str(rows[7].id), str(rows[0].id), str(rows[1].id)]  # attached, then the topic's product
    assert c.attached == [str(rows[6].id), str(rows[7].id)]  # attached with issues is still used
    reasons = dict(c.skipped)
    assert "another brand (Nike)" in reasons[str(rows[8].id)] and any("isn't one of" in r for r in reasons.values())


async def test_library_photos_are_capped_and_the_topics_product_comes_first(db_maker):
    org, brand, rows = await seed(db_maker, [{"product": None}] * 8 + [{"product": "Masala Chai Cold Brew"}])
    async with db_maker() as db:
        c = await candidates(db, org, brand, BRIEF)
    assert len(c.assets) == 6 and c.assets[0].product == "Masala Chai Cold Brew" and c.attached == []


# --- the page that gets rendered ---------------------------------------------------------


async def test_the_page_uses_the_approved_words_the_kit_and_the_chosen_photo(db_maker):
    org, brand, rows = await seed(db_maker, [{"product": "Masala Chai Cold Brew", "description": "A glass."}])
    store = MemoryStorage()
    store.write_bytes(rows[0].key, photo().data, "image/jpeg")
    draft = make_draft(image_text=ImageText(headline="Cold chai weather, anyone?", subline="New on the menu"))
    async with db_maker() as db:
        c = await candidates(db, org, brand, BRIEF)
        inp_ = design_input(brand, BRIEF, draft, c)
        assert inp_.headline == "Cold chai weather, anyone?" and inp_.photos[0].product == "Masala Chai Cold Brew"
        spec = await page_spec(db, store, org, brand, BRIEF, draft, choice(photo_id=str(rows[0].id), text_area="top", focus="bottom"), c)
    assert (spec.headline, spec.subline, spec.layout) == ("Cold chai weather, anyone?", "New on the menu", "photo")
    assert spec.photo is not None and spec.photo_focus == "center 80%" and spec.design.fonts.heading.family == "Fraunces"
    assert spec.canvas.width == 1080 and spec.logo is None  # no logo uploaded: the wordmark stands in
    with pytest.raises(ValueError, match="isn't one of the candidates"):
        async with db_maker() as db:
            await page_spec(db, store, org, brand, BRIEF, draft, choice(photo_id="nope"), c)
