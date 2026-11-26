"""Rendering post images: HTML layouts, embedded fonts, and the browser render with its measurements."""

import asyncio
import base64
from io import BytesIO
from pathlib import Path

import httpx
import pytest
import yaml
from PIL import Image as PILImage

from buildsocial.design_specs import TYPE, canvas_for
from buildsocial.harness.storage import MemoryStorage
from buildsocial.render import fonts
from buildsocial.render.layouts import LAYOUTS, Image, PageSpec, build_html, readable_on
from buildsocial.schemas import BrandProfile

EXAMPLES = Path(__file__).parent.parent / "examples"
BREW = BrandProfile.model_validate(yaml.safe_load((EXAMPLES / "brew-house.yaml").read_text()))


def photo() -> Image:
    out = BytesIO()
    PILImage.new("RGB", (800, 600), (201, 119, 47)).save(out, "JPEG")
    return Image(out.getvalue(), "image/jpeg")


def spec(layout="type_led", **change) -> PageSpec:
    base = dict(layout=layout, headline="Cold chai weather, anyone?", subline="Masala Chai Cold Brew, new on the menu",
                brand_name="Brew House", design=BREW.design, canvas=canvas_for("instagram", "single_image"),
                photo=None if layout == "type_led" else photo())
    return PageSpec(**(base | change))


# --- layouts ------------------------------------------------------------------------------


def test_words_are_escaped_and_exact():
    html = build_html(spec(headline='Chai <script>alert(1)</script> & "more"'), font_css="")
    assert "<script>alert" not in html
    assert "Chai &lt;script&gt;alert(1)&lt;/script&gt; &amp; &quot;more&quot;" in html


def test_every_layout_builds_and_uses_the_kit():
    for layout in LAYOUTS:
        html = build_html(spec(layout), font_css="")
        assert "'Fraunces'" in html and "'DM Sans'" in html
        assert "Brew House" in html  # wordmark stands in until a logo is uploaded
        assert "http://" not in html and "https://" not in html  # nothing loads from the network


def test_photo_layouts_need_a_photo():
    for layout in ("photo", "split", "framed"):
        with pytest.raises(ValueError, match="needs a photo"):
            build_html(spec(layout, photo=None), font_css="")


def test_text_colour_flips_on_dark_fields():
    assert readable_on("#2E1D14", BREW.design) == "#F3EADB"  # cream on espresso
    assert readable_on("#C9772F", BREW.design) == "#2E1D14"  # espresso on chai
    assert "color: #F3EADB" in build_html(spec("framed"), font_css="")


def test_framed_puts_the_subline_under_the_photo():
    html = build_html(spec("framed"), font_css="")
    hbox = html.split("<div id='hbox'>")[1].split("</div>")[0]
    assert "subline" not in hbox and 'id="subline"' in html


# --- fonts --------------------------------------------------------------------------------

CSS = """/* vietnamese */
@font-face { font-family: 'Fraunces'; src: url(https://fonts.gstatic.com/s/f/vi.woff2) format('woff2'); }
/* latin-ext */
@font-face { font-family: 'Fraunces'; src: url(https://fonts.gstatic.com/s/f/ext.woff2) format('woff2'); }
/* latin */
@font-face { font-family: 'Fraunces'; src: url(https://fonts.gstatic.com/s/f/latin.woff2) format('woff2'); }
"""


def test_fonts_keep_latin_inline_the_files_and_cache():
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        if "css2" in str(request.url):
            return httpx.Response(200, text=CSS)
        return httpx.Response(200, content=b"WOFF2-" + request.url.path.encode())

    store = MemoryStorage()

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            first = await fonts.font_css(store, "Fraunces", 800, client)
            again = await fonts.font_css(store, "Fraunces", 800, client)
        return first, again

    css, again = asyncio.run(run())
    assert css == again and len(seen) == 3  # css + 2 files, then the cache
    assert "vi.woff2" not in "".join(seen)  # Vietnamese subset skipped
    assert "gstatic" not in css and css.count("data:font/woff2;base64,") == 2
    assert base64.b64encode(b"WOFF2-/s/f/latin.woff2").decode() in css
    assert store.exists("fonts/fraunces-800.css")


def test_unknown_font_is_a_clear_error():
    transport = httpx.MockTransport(lambda r: httpx.Response(400, text="bad family"))

    async def run():
        async with httpx.AsyncClient(transport=transport) as client:
            await fonts.font_css(MemoryStorage(), "Not A Font", 400, client)

    with pytest.raises(fonts.FontNotFound, match="no 'Not A Font'"):
        asyncio.run(run())


# --- the browser render (skipped when the browser isn't installed) -------------------------


def _browser_installed() -> bool:
    return any((Path.home() / ".cache" / "ms-playwright").glob("chromium*"))


@pytest.mark.skipif(not _browser_installed(), reason="run: uv run playwright install --only-shell chromium")
def test_render_fits_the_headline_inside_the_safe_box():
    from buildsocial.render.renderer import Renderer

    long_headline = "A much longer headline that has to shrink to fit"

    async def run():
        async with Renderer() as r:
            return await r.render(spec("split"), ""), await r.render(spec("type_led", headline=long_headline), "")

    split, long = asyncio.run(run())
    with PILImage.open(BytesIO(split.png)) as im:
        assert im.size == (1080, 1350)
    safe = canvas_for("instagram", "single_image").safe
    for rendered in (split, long):
        assert not rendered.overflow
        assert TYPE.headline_px[0] <= rendered.headline_px <= TYPE.headline_px[1]
        for name in ("headline", "subline", "logo"):
            b = rendered.boxes[name]
            assert safe.left <= b["left"] and b["right"] <= safe.right + 1, name
            assert safe.top <= b["top"] and b["bottom"] <= safe.bottom + 1, name
    assert long.headline_px < TYPE.headline_px[1]  # it shrank
