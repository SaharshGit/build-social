"""The four single-image layouts as HTML/CSS, filled with a brand's kit (see skills/design/formats/single_image.md).

Every position comes from design_specs (canvas, safe box, type sizes, logo size), so a layout can't put
text where the visual check would fail it. Words are inserted escaped and exactly as approved; the
browser only chooses the headline size (fit_headline in renderer.py) and where lines break.

    photo     the photo fills the canvas; the headline sits in its calm area (top or bottom)
    split     photo on the top part, a kit-colour panel below holds the text
    framed    the photo sits inside a kit-colour field, headline above, subline below
    type_led  a kit-colour field where the headline is the design; no photo needed
"""

import base64
from dataclasses import dataclass, field
from html import escape
from typing import Literal

from buildsocial.design_specs import LOGO, TYPE, CanvasSpec
from buildsocial.schemas import BrandDesign, contrast_ratio

Layout = Literal["photo", "split", "framed", "type_led"]
LAYOUTS: tuple[Layout, ...] = ("photo", "split", "framed", "type_led")


@dataclass
class Image:
    data: bytes
    mime: str

    def uri(self) -> str:
        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode('ascii')}"


@dataclass
class PageSpec:
    """Everything one render needs. The design step fills this in; code checks it before rendering."""

    layout: Layout
    headline: str
    subline: str | None
    brand_name: str
    design: BrandDesign
    canvas: CanvasSpec
    photo: Image | None = None
    logo: Image | None = None
    text_area: Literal["top", "bottom"] = "top"  # photo layout: where the photo is calm
    photo_focus: str = "center"  # CSS object-position, e.g. "center bottom": keeps the product in view
    field_role: Literal["background", "text", "accent", "secondary", "highlight"] = "accent"  # type_led field
    scrim: bool = False  # photo layout: a soft kit-colour gradient behind the text when contrast needs it
    extra_css: list[str] = field(default_factory=list)


def readable_on(bg_hex: str, design: BrandDesign) -> str:
    """The kit's text or background colour, whichever reads better on bg_hex."""
    candidates = [c.hex for c in design.colors if c.role in ("text", "background")]
    return max(candidates, key=lambda c: contrast_ratio(c, bg_hex))


def colors(spec: PageSpec) -> tuple[str, str]:
    """(page background, text colour) the layout uses: the checks measure contrast against these."""
    d = spec.design
    bg, text = d.color("background").hex, d.color("text").hex
    if spec.layout == "framed":  # the dark kit colour frames the photo; text flips to the light one
        return text, readable_on(text, d)
    if spec.layout == "type_led":
        field_color = d.color(spec.field_role)
        page_bg = field_color.hex if field_color else bg
        return page_bg, readable_on(page_bg, d)
    return bg, text  # photo: the design step picks a photo whose calm area suits the kit's text colour


def build_html(spec: PageSpec, font_css: str) -> str:
    d, c, s = spec.design, spec.canvas, spec.canvas.safe
    bg = d.color("background").hex
    text = d.color("text").hex
    heading, body = d.fonts.heading, d.fonts.body
    head_w, body_w = max(heading.weights or [700]), min(body.weights or [400])

    page_bg, text_color = colors(spec)
    inner = ""
    if spec.layout == "photo":
        if spec.photo is None:
            raise ValueError("the photo layout needs a photo")
        scrim_side = "to bottom" if spec.text_area == "top" else "to top"
        scrim = (f'<div class="scrim" style="background: linear-gradient({scrim_side}, {bg}e6 0%, {bg}00 100%)"></div>'
                 if spec.scrim else "")
        inner = (f'<img class="photo full" src="{spec.photo.uri()}" style="object-position: {escape(spec.photo_focus)}" alt="">'
                 f"{scrim}")
    elif spec.layout == "split":
        if spec.photo is None:
            raise ValueError("the split layout needs a photo")
        inner = f'<img class="photo split" src="{spec.photo.uri()}" style="object-position: {escape(spec.photo_focus)}" alt="">'
    elif spec.layout == "framed":
        if spec.photo is None:
            raise ValueError("the framed layout needs a photo")
        inner = f'<img class="photo framed" src="{spec.photo.uri()}" style="object-position: {escape(spec.photo_focus)}" alt="">'
    elif spec.layout == "type_led":
        pass
    else:
        raise ValueError(f"unknown layout {spec.layout}")

    # Where the text block sits, per layout (inside the safe box).
    split_line = round(c.height * 0.58)
    if spec.layout == "photo":
        band = round(c.height * 0.36)
        top = s.top + 40 if spec.text_area == "top" else s.bottom - 100 - band
        hbox = f"top:{top}px; height:{band}px;"
    elif spec.layout == "split":
        hbox = f"top:{split_line + 56}px; height:{s.bottom - split_line - 56 - 110}px;"
    elif spec.layout == "framed":
        hbox = f"top:{s.top + 40}px; height:{round(c.height * 0.24)}px;"
    else:  # type_led: the text block is the design, centred in the space above the logo
        hbox = f"top:{s.top}px; height:{s.bottom - 120 - s.top}px;"

    logo_h = round(c.width * LOGO.width_share[1] * 0.35)
    if spec.logo:
        logo = (f'<img id="logo" class="logo" src="{spec.logo.uri()}" alt="{escape(spec.brand_name)}" '
                f'style="max-height:{logo_h}px; max-width:{round(c.width * LOGO.width_share[1])}px">')
    else:  # no logo uploaded yet: the brand name in its heading font stands in
        logo = f'<div id="logo" class="logo wordmark">{escape(spec.brand_name)}</div>'
    plate = spec.layout == "photo"  # on a photo the logo sits on a small kit-colour plate

    sub = f'<p id="subline">{escape(spec.subline)}</p>' if spec.subline else ""
    below = ""
    if spec.layout == "framed" and sub:  # framed: the subline sits under the photo, not with the headline
        photo_bottom = s.top + 40 + round(c.height * 0.24) + 40 + round(s.width * 0.75)
        below = f'<div style="position:absolute; left:{s.left}px; width:{s.width}px; top:{photo_bottom + 8}px">{sub}</div>'
        sub = ""
    css = f"""
{font_css}
* {{ box-sizing: border-box; margin: 0; padding: 0; }}
html, body {{ width: {c.width}px; height: {c.height}px; overflow: hidden; }}
body {{ background: {page_bg}; color: {text_color}; font-family: '{body.family}', sans-serif; font-weight: {body_w};
        position: relative; -webkit-font-smoothing: antialiased; }}
.photo {{ position: absolute; object-fit: cover; display: block; }}
.photo.full {{ inset: 0; width: 100%; height: 100%; }}
.photo.split {{ left: 0; top: 0; width: 100%; height: {split_line}px; }}
.photo.framed {{ left: {s.left}px; width: {s.width}px; top: {s.top + 40 + round(c.height * 0.24) + 40}px;
                 height: {round(s.width * 0.75)}px; }}
.scrim {{ position: absolute; left: 0; right: 0; height: 55%; {'top: 0' if spec.text_area == 'top' else 'bottom: 0'}; }}
#hbox {{ position: absolute; left: {s.left}px; width: {s.width}px; {hbox} display: flex; flex-direction: column;
         justify-content: {'flex-end' if spec.layout == 'photo' and spec.text_area == 'bottom'
                           else 'center' if spec.layout == 'type_led' else 'flex-start'}; }}
#headline {{ font-family: '{heading.family}', serif; font-weight: {head_w}; line-height: 1.04; letter-spacing: -0.01em;
             font-size: {TYPE.headline_px[1]}px; overflow-wrap: break-word; }}
#subline {{ font-size: {TYPE.subline_px[0] + 4}px; line-height: 1.3; margin-top: 28px; }}
.logo {{ position: absolute; right: {c.width - s.right}px; bottom: {c.height - s.bottom}px; }}
.wordmark {{ font-family: '{heading.family}', serif; font-weight: {head_w}; font-size: {TYPE.min_text_px}px;
             line-height: 1; {'background:' + bg + '; color:' + text + '; padding: 12px 20px; border-radius: 999px;' if plate else ''} }}
img.logo {{ {'background:' + bg + '; padding: 10px 16px; border-radius: 14px;' if plate else ''} }}
{''.join(spec.extra_css)}
"""
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>{css}</style></head><body>"
            f"{inner}<div id='hbox'><h1 id='headline'>{escape(spec.headline)}</h1>{sub}</div>{below}{logo}</body></html>")
