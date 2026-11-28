"""Making a post's image: attempts and retries, stored result, and what the worker and API do with it."""

import json
import uuid
from io import BytesIO

import pytest
from PIL import Image as PILImage

from buildsocial.agents.designer import DesignChoice
from buildsocial.agents.visual_check import CHECK_NAMES, VisualVerdict
from buildsocial.db.models import Post
from buildsocial.harness.llm.base import LLMError
from buildsocial.harness.storage import MemoryStorage
from buildsocial.harness.trace import Tracer
from buildsocial.imaging import ImageDeps, ImageError, make_image, needs_new_image, visual_result
from buildsocial.pipeline import StepRunner
from buildsocial.render.layouts import colors
from buildsocial.render.renderer import Rendered
from buildsocial.schemas import ImageText, PostBrief
from tests.test_api_runs import INR
from tests.test_content_agent import make_draft
from tests.test_design import READ_OK, seed
from tests.test_photo_reader import RecordingLLM
from tests.test_render import BREW, photo

HEADLINE, SUBLINE = "Cold chai weather, anyone?", "Masala Chai Cold Brew, new on the menu"
BRIEF = PostBrief(topic="new Masala Chai Cold Brew", platform="instagram", format="single_image")
DRAFT = make_draft(image_text=ImageText(headline=HEADLINE, subline=SUBLINE))
BOXES = {"headline": {"left": 98, "top": 300, "right": 900, "bottom": 600},
         "subline": {"left": 98, "top": 640, "right": 800, "bottom": 690},
         "logo": {"left": 800, "top": 1240, "right": 982, "bottom": 1286}}


class FakeRenderer:
    """Stands in for the browser: a flat page in the layout's own background colour, text boxes inside the safe box."""

    def __init__(self):
        self.rendered = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return None

    async def render(self, spec, font_css):
        self.rendered.append(spec.layout)
        out = BytesIO()
        PILImage.new("RGB", (1080, 1350), colors(spec)[0]).save(out, "PNG")
        return Rendered(png=out.getvalue(), background_png=out.getvalue(), headline_px=100, headline_lines=2,
                        overflow=False, boxes=BOXES, subline_px=40)


async def no_fonts(store, families):
    return ""


def deps(store=None, renderer=None) -> ImageDeps:
    return ImageDeps(store=store or MemoryStorage(), renderer=lambda: renderer or FakeRenderer(), font_css=no_fonts)


def design(**change) -> str:
    base = dict(directions=["a", "b", "c"], rationale="r", layout="split", photo_id=None, text_area=None, focus="center",
                field_role=None, scrim=False)
    return DesignChoice(**(base | change)).model_dump_json()


def verdict(headline=HEADLINE, subline=SUBLINE, fail=()) -> str:
    return json.dumps({"headline_as_read": headline, "subline_as_read": subline,
                       "checks": [{"name": n, "reason": "bad" if n in fail else "fine", "passed": n not in fail}
                                  for n in CHECK_NAMES]})


class Models:
    """Scripted models by agent name; remembers every call."""

    def __init__(self, **replies):
        self.llms = {name: RecordingLLM(list(r)) for name, r in replies.items()}

    def client_for(self, name):
        return self.llms[name]

    def runner(self, progress=None):
        return StepRunner(Tracer(), self.client_for, lambda n: INR, progress)


async def setup(db_maker, with_photo=True):
    org, brand, rows = await seed(db_maker, [{"product": "Masala Chai Cold Brew", "description": "A glass."}]
                                  if with_photo else [])
    store = MemoryStorage()
    if with_photo:
        store.write_bytes(rows[0].key, photo().data, "image/jpeg")
    return org, brand, rows, store


async def make(db_maker, org, brand, models, store, *, previous=None, other_layout=False, renderer=None, draft=DRAFT):
    async with db_maker() as db:
        return await make_image(db, deps(store, renderer), models.runner(), org, brand, uuid.uuid4(), BRIEF, draft,
                                previous=previous, other_layout=other_layout)


# --- attempts -------------------------------------------------------------------------


async def test_a_clean_first_attempt_is_stored(db_maker):
    org, brand, rows, store = await setup(db_maker)
    models = Models(designer=[design(photo_id=str(rows[0].id))], visual_check=[verdict()])
    image = await make(db_maker, org, brand, models, store)

    assert (image["status"], image["version"], image["layout"]) == ("ok", 1, "split")
    assert len(image["attempts"]) == 1 and image["attempts"][0]["problems"] == []
    assert {c["name"] for c in image["checks"]} >= {"fits", "contrast", "calm_behind"} and image["visual"]["read"]["headline"] == HEADLINE
    assert image["words"] == {"headline": HEADLINE, "subline": SUBLINE} and image["photo_ids"] == [str(rows[0].id)]
    assert store.read_bytes(image["key"])[:4] == b"\x89PNG"
    sent = models.llms["visual_check"].sent[0][-1]  # the model really got the image, downscaled
    assert sent.images and sent.images[0].mime_type == "image/jpeg"


async def test_a_visual_failure_retries_with_the_reasons_and_a_different_layout(db_maker):
    org, brand, rows, store = await setup(db_maker)
    pid = str(rows[0].id)
    models = Models(designer=[design(photo_id=pid), design(layout="framed", photo_id=pid)],
                    visual_check=[verdict(fail=["not_generic"]), verdict()])
    renderer = FakeRenderer()
    image = await make(db_maker, org, brand, models, store, renderer=renderer)

    assert image["status"] == "ok" and image["layout"] == "framed" and renderer.rendered == ["split", "framed"]
    retry_prompt = models.llms["designer"].sent[1][-1].content
    assert "The previous design ('split') did not work" in retry_prompt and "not_generic: bad" in retry_prompt
    assert image["attempts"][0]["problems"] == ["not_generic: bad"]


async def test_misread_words_fail_the_words_check_by_comparison(db_maker):
    org, brand, rows, store = await setup(db_maker)
    pid = str(rows[0].id)
    # The model says words_exact passed, but what it READ isn't the approved headline.
    models = Models(designer=[design(photo_id=pid), design(layout="framed", photo_id=pid)],
                    visual_check=[verdict(headline="Cold chai weather, anyone"), verdict()])
    image = await make(db_maker, org, brand, models, store)
    assert image["attempts"][0]["problems"][0].startswith("words_exact: headline reads 'Cold chai weather, anyone'")
    assert image["layout"] == "framed"


async def test_when_nothing_passes_the_least_bad_is_kept_for_a_person(db_maker):
    org, brand, rows, store = await setup(db_maker)
    pid = str(rows[0].id)
    models = Models(designer=[design(layout="type_led", field_role="accent"), design(layout="framed", photo_id=pid)],
                    visual_check=[verdict(fail=["one_focus", "style_match"]), verdict(fail=["one_focus"]),
                                  verdict(fail=["one_focus", "not_generic"])])
    image = await make(db_maker, org, brand, models, store)
    assert image["status"] == "needs_attention" and len(image["attempts"]) == 3
    assert image["layout"] == "framed"  # one failed visual check, the fewest
    assert [a["layout"] for a in image["attempts"]] == ["type_led", "framed", "split"]  # the 3rd is the rule-based fallback


async def test_the_same_design_is_never_rendered_twice(db_maker):
    org, brand, rows, store = await setup(db_maker)
    pid = str(rows[0].id)  # the rule-based fallback IS split + this photo, which attempt 1 already tried
    models = Models(designer=[design(photo_id=pid), design(layout="framed", photo_id=pid)],
                    visual_check=[verdict(fail=["one_focus"]), verdict(fail=["one_focus", "style_match"])])
    renderer = FakeRenderer()
    image = await make(db_maker, org, brand, models, store, renderer=renderer)
    assert renderer.rendered == ["split", "framed"] and image["layout"] == "split"


async def test_a_failing_design_agent_falls_back_to_a_rule_based_design(db_maker):
    org, brand, rows, store = await setup(db_maker)
    models = Models(designer=[LLMError("down")], visual_check=[verdict()])
    image = await make(db_maker, org, brand, models, store)
    assert image["status"] == "ok" and image["layout"] == "split"
    assert any("design agent failed" in a.get("problem", "") for a in image["attempts"])


async def test_a_failed_visual_check_model_keeps_the_image_unchecked(db_maker):
    org, brand, rows, store = await setup(db_maker, with_photo=False)
    models = Models(designer=[design(layout="type_led", field_role="accent")], visual_check=[LLMError("down")])
    image = await make(db_maker, org, brand, models, store)
    assert image["status"] == "ok" and "error" in image["visual"] and image["layout"] == "type_led"


async def test_unmakeable_images_say_why(db_maker):
    org, brand, rows, store = await setup(db_maker)
    with pytest.raises(ImageError, match="no words for the image"):
        await make(db_maker, org, brand, Models(), store, draft=make_draft())
    brand.profile = BREW.model_copy(update={"design": None}).model_dump(mode="json")
    with pytest.raises(ImageError, match="no look & feel yet"):
        await make(db_maker, org, brand, Models(), store)


# --- revisions and "try another layout" ----------------------------------------------------


async def test_new_words_keep_the_earlier_design_and_need_no_design_call(db_maker):
    org, brand, rows, store = await setup(db_maker)
    pid = str(rows[0].id)
    first = await make(db_maker, org, brand, Models(designer=[design(photo_id=pid)], visual_check=[verdict()]), store)
    revised = make_draft(image_text=ImageText(headline="Chai, but cold", subline=SUBLINE))
    models = Models(designer=[], visual_check=[verdict(headline="Chai, but cold")])  # no designer replies at all
    second = await make(db_maker, org, brand, models, store, previous=first, draft=revised)
    assert second["version"] == 2 and second["layout"] == first["layout"] and second["words"]["headline"] == "Chai, but cold"
    assert second["key"] != first["key"] and store.exists(first["key"])  # the earlier image is kept


async def test_try_another_layout_must_choose_differently(db_maker):
    org, brand, rows, store = await setup(db_maker)
    pid = str(rows[0].id)
    first = await make(db_maker, org, brand, Models(designer=[design(photo_id=pid)], visual_check=[verdict()]), store)
    models = Models(designer=[design(photo_id=pid), design(layout="framed", photo_id=pid)], visual_check=[verdict()])
    second = await make(db_maker, org, brand, models, store, previous=first, other_layout=True)
    assert second["layout"] == "framed" and second["version"] == 2
    assert "'split' just failed; choose a different layout" in models.llms["designer"].sent[1][-1].content  # the retry reason


def test_when_an_image_is_out_of_date():
    post = Post(image=None)
    assert needs_new_image(post, DRAFT)  # no image yet
    post.image = {"key": "k", "words": {"headline": HEADLINE, "subline": SUBLINE}}
    assert not needs_new_image(post, DRAFT)
    assert needs_new_image(post, make_draft(image_text=ImageText(headline="New", subline=SUBLINE)))
    assert needs_new_image(Post(image=post.image | {"error": "x"}), DRAFT)  # failed last time: try again


def test_visual_result_orders_the_checks_and_compares_the_words():
    v = VisualVerdict.model_validate_json(verdict(subline="Masala Chai Cold Brew new on the menu")[::1])
    r = visual_result(v, HEADLINE, SUBLINE)
    assert [c["name"] for c in r["checks"]] == list(CHECK_NAMES)
    words = next(c for c in r["checks"] if c["name"] == "words_exact")
    assert not words["passed"] and "subline reads" in words["reason"]
    ok = visual_result(VisualVerdict.model_validate_json(verdict(HEADLINE.upper(), SUBLINE)), HEADLINE, SUBLINE)
    assert next(c for c in ok["checks"] if c["name"] == "words_exact")["passed"]  # case doesn't matter
