"""Design numbers and design skills: consistent with each other and with the formats we offer."""

from typing import get_args

import pytest

from buildsocial.agents.content import SKILLS_DIR
from buildsocial.design_specs import CANVAS, IG_GRID_CROP, MARGIN, TYPE, canvas_for, specs_text
from buildsocial.harness.skills import render_skills
from buildsocial.schemas import Format, Platform

VISUAL_FORMATS = [f for f in get_args(Format) if f != "text"]
DESIGN_SKILLS = ["design/art_direction", "design/layout", "design/anti_generic", "design/review"]


@pytest.mark.parametrize("key", list(CANVAS))
def test_safe_box_is_inside_the_canvas_with_a_margin(key):
    c = CANVAS[key]
    assert c.box.contains(c.safe)
    assert min(c.safe.left, c.safe.top, c.width - c.safe.right, c.height - c.safe.bottom) >= MARGIN


def test_instagram_text_survives_the_3_4_grid_crop():
    assert IG_GRID_CROP.width == 1012 and IG_GRID_CROP.height == 1350  # 3:4 of a 1350 px-tall post
    safe = canvas_for("instagram", "single_image").safe
    assert IG_GRID_CROP.contains(safe)
    assert safe.left - IG_GRID_CROP.left >= MARGIN and IG_GRID_CROP.right - safe.right >= MARGIN


def test_every_visual_format_has_a_canvas_per_platform_and_a_design_skill():
    for fmt in VISUAL_FORMATS:
        render_skills(SKILLS_DIR, [f"design/formats/{fmt}"])  # raises if the skill file is missing
        for platform in get_args(Platform):
            canvas_for(platform, fmt)


def test_design_skills_load_and_hold_no_numbers_that_belong_in_specs():
    text = render_skills(SKILLS_DIR, DESIGN_SKILLS + [f"design/formats/{f}" for f in VISUAL_FORMATS])
    assert "## Design: layout" in text and "## Design review" in text
    for number in ["1080", "1350", f"{TYPE.min_text_px}px", f"{TYPE.min_text_px} px", "4.5"]:
        assert number not in text, f"'{number}' belongs in design_specs.py, not a skill"


def test_type_limits_are_coherent():
    assert TYPE.min_text_px <= TYPE.subline_px[0] < TYPE.subline_px[1] < TYPE.headline_px[0] < TYPE.headline_px[1]
    assert TYPE.large_text_px <= TYPE.headline_px[0]  # every headline counts as large text


def test_specs_text_states_the_numbers():
    text = specs_text("instagram", "single_image")
    assert "1080x1350" in text and "x 98-982" in text and "3.0:1" in text
    with pytest.raises(ValueError, match="no canvas"):
        canvas_for("instagram", "reel")
