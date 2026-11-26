"""Measured checks on rendered images, on synthetic renders (no browser needed)."""

from io import BytesIO

from PIL import Image as PILImage
from PIL import ImageDraw

from buildsocial.render.checks import failed, run_checks
from buildsocial.render.renderer import Rendered
from tests.test_render import BREW, photo, spec

CREAM, ESPRESSO = (243, 234, 219), (46, 29, 20)
HEADLINE = {"left": 98, "top": 300, "right": 900, "bottom": 600}
SUBLINE = {"left": 98, "top": 640, "right": 800, "bottom": 690}
LOGO = {"left": 800, "top": 1240, "right": 982, "bottom": 1286}


def png(draw=None, color=CREAM) -> bytes:
    im = PILImage.new("RGB", (1080, 1350), color)
    if draw:
        draw(ImageDraw.Draw(im))
    out = BytesIO()
    im.save(out, "PNG")
    return out.getvalue()


def rendered(background=None, **change) -> Rendered:
    base = dict(png=b"", background_png=background or png(), headline_px=120, headline_lines=2, overflow=False,
                boxes={"headline": HEADLINE, "subline": SUBLINE, "logo": LOGO}, subline_px=40)
    return Rendered(**(base | change))


CREAM_FIELD = spec("type_led", field_role="background")  # espresso text on cream


def names(checks):
    return {c.name for c in failed(checks)}


def test_a_clean_render_passes_everything():
    checks = run_checks(CREAM_FIELD, rendered())
    assert failed(checks) == [], failed(checks)
    assert {c.name for c in checks} == {"fits", "inside_safe_box", "text_sizes", "contrast", "calm_behind", "logo_size"}


def test_low_contrast_behind_the_text_fails():
    # A light-brown block right behind the subline: espresso text on it is too faint for small text.
    bg = png(lambda d: d.rectangle((90, 630, 820, 700), fill=(120, 90, 70)))
    checks = run_checks(CREAM_FIELD, rendered(bg))
    bad = [c for c in failed(checks) if c.name == "contrast"]
    assert bad and "subline" in bad[0].detail and "needs 4.5:1" in bad[0].detail


def test_text_over_a_busy_area_fails():
    def stripes(d):  # an object's outlines behind the headline
        for x in range(100, 900, 12):
            d.line((x, 300, x, 600), fill=(150, 150, 150), width=2)
    checks = run_checks(CREAM_FIELD, rendered(png(stripes)))
    assert "calm_behind" in names(checks)


def test_a_soft_gradient_is_still_calm():
    def gradient(d):
        for y in range(300, 600):
            shade = 243 - (y - 300) // 20
            d.line((0, y, 1080, y), fill=(shade, shade - 9, shade - 24))
    assert "calm_behind" not in names(run_checks(CREAM_FIELD, rendered(png(gradient))))


def test_layout_problems_are_reported():
    checks = run_checks(CREAM_FIELD, rendered(
        overflow=True, headline_px=72, headline_lines=4, subline_px=30,
        boxes={"headline": HEADLINE | {"left": 40}, "subline": SUBLINE, "logo": LOGO}))
    assert names(checks) >= {"fits", "inside_safe_box", "text_sizes"}
    sizes = next(c for c in checks if c.name == "text_sizes")
    assert "4 lines" in sizes.detail and "subline 30 px" in sizes.detail


def test_logo_limits_differ_for_image_logos_and_wordmarks():
    wide = {"logo": {"left": 760, "top": 1240, "right": 982, "bottom": 1286}}  # 21% of the width
    boxes = {"headline": HEADLINE, "subline": SUBLINE} | wide
    assert "logo_size" not in names(run_checks(CREAM_FIELD, rendered(boxes=boxes)))  # wordmark: up to 24%
    with_logo = spec("type_led", field_role="background", logo=photo())
    assert "logo_size" in names(run_checks(with_logo, rendered(boxes=boxes)))  # image logo: up to 14%


def test_contrast_uses_the_colour_the_layout_actually_uses():
    # Framed: cream text on the espresso field. On an espresso background that's readable.
    checks = run_checks(spec("framed"), rendered(png(color=ESPRESSO)))
    assert "contrast" not in names(checks)
    assert BREW.design.color("text").hex == "#2E1D14"
