"""Measured checks on a rendered image: free, exact, and run on every render before any model looks at it.

    fits             the text fit its box at a headline size within the limits
    inside_safe_box  headline, subline and logo all sit inside the safe box
    text_sizes       headline and subline sizes, headline line count
    contrast         text against the real pixels behind it (the page rendered with the words hidden),
                     on the worst CONTRAST_PERCENTILE of those pixels, not the average
    calm_behind      few strong edges behind each text block (no product outline or clutter under the words)
    logo_size        the logo isn't larger than the spec allows

The visual check (a model) judges what code can't: style, focus, whether it looks generic.
"""

from dataclasses import dataclass
from io import BytesIO

from PIL import Image as PILImage
from PIL import ImageFilter

from buildsocial.design_specs import CONTRAST_PERCENTILE, EDGE_LEVEL, LOGO, MAX_EDGE_SHARE, TYPE, Box
from buildsocial.render.layouts import PageSpec, colors
from buildsocial.render.renderer import Rendered

SAMPLE_MAX = 160  # downscale each text area to at most this many pixels wide before measuring


@dataclass(frozen=True)
class Check:
    name: str
    passed: bool
    detail: str


def _luminance(rgb: tuple[int, int, int]) -> float:
    def channel(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = rgb
    return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b)


def _hex_lum(hex_color: str) -> float:
    return _luminance(tuple(int(hex_color[i:i + 2], 16) for i in (1, 3, 5)))  # type: ignore[arg-type]


def _box(b: dict | None) -> Box | None:
    return Box(round(b["left"]), round(b["top"]), round(b["right"]), round(b["bottom"])) if b else None


def contrast_behind(background: PILImage.Image, box: Box, text_hex: str) -> float:
    """Contrast of the text colour against the worst CONTRAST_PERCENTILE of the pixels behind one box."""
    area = background.crop((box.left, box.top, box.right, box.bottom))
    if area.width > SAMPLE_MAX:
        area = area.resize((SAMPLE_MAX, max(1, round(area.height * SAMPLE_MAX / area.width))))
    text = _hex_lum(text_hex)
    ratios = sorted((max(l, text) + 0.05) / (min(l, text) + 0.05)
                    for l in (_luminance(px) for px in area.convert("RGB").get_flattened_data()))
    return ratios[max(0, len(ratios) * CONTRAST_PERCENTILE // 100 - 1)]


def edge_share(edges: PILImage.Image, box: Box, scale: int = 2) -> float:
    """Share of strong edges behind one box. `edges` is the whole page, edge-filtered at 1/scale size
    (filtering before cropping, so the crop's own border doesn't count as an edge)."""
    area = edges.crop((box.left // scale + 1, box.top // scale + 1, box.right // scale - 1, box.bottom // scale - 1))
    px = list(area.get_flattened_data())
    return sum(1 for p in px if p > EDGE_LEVEL) / len(px) if px else 0.0


def run_checks(spec: PageSpec, out: Rendered) -> list[Check]:
    safe, checks = spec.canvas.safe, []
    boxes = {name: _box(b) for name, b in out.boxes.items()}

    checks.append(Check("fits", not out.overflow,
                        f"headline {out.headline_px:.0f} px" if not out.overflow
                        else f"the text doesn't fit even at {TYPE.headline_px[0]} px; shorten it or use another layout"))

    outside = [name for name, b in boxes.items()
               if b and not (safe.left - 1 <= b.left and safe.top - 1 <= b.top
                             and b.right <= safe.right + 1 and b.bottom <= safe.bottom + 1)]
    checks.append(Check("inside_safe_box", not outside,
                        "all inside" if not outside else f"outside the safe box: {', '.join(outside)}"))

    problems = []
    if not TYPE.headline_px[0] <= out.headline_px <= TYPE.headline_px[1]:
        problems.append(f"headline {out.headline_px:.0f} px")
    if out.headline_lines > TYPE.max_headline_lines:
        problems.append(f"headline has {out.headline_lines} lines")
    if out.subline_px is not None and not TYPE.subline_px[0] <= out.subline_px <= TYPE.subline_px[1]:
        problems.append(f"subline {out.subline_px:.0f} px")
    checks.append(Check("text_sizes", not problems, "; ".join(problems) or "within limits"))

    _, text_hex = colors(spec)
    with PILImage.open(BytesIO(out.background_png)) as bg_img:
        bg_img = bg_img.convert("RGB")
        small = bg_img.convert("L").resize((bg_img.width // 2, bg_img.height // 2))
        edges = small.filter(ImageFilter.FIND_EDGES)
        low, busy = [], []
        for name, need in (("headline", TYPE.contrast_large), ("subline", TYPE.contrast_normal)):
            b = boxes.get(name)
            if not b or b.width <= 4 or b.height <= 4:
                continue
            ratio = contrast_behind(bg_img, b, text_hex)
            if ratio < need:
                low.append(f"{name} {ratio:.1f}:1 (needs {need}:1)")
            share = edge_share(edges, b)
            if share > MAX_EDGE_SHARE:
                busy.append(f"{name} ({share:.1%} strong edges, max {MAX_EDGE_SHARE:.0%})")
    checks.append(Check("contrast", not low, "; ".join(low) or "readable"))
    checks.append(Check("calm_behind", not busy,
                        ("text over a busy area: " + "; ".join(busy)) if busy else "calm"))

    logo = boxes.get("logo")
    if logo:
        share = logo.width / spec.canvas.width
        limit = LOGO.width_share[1] if spec.logo else LOGO.wordmark_max_share  # no logo: the name as a wordmark
        checks.append(Check("logo_size", share <= limit + 0.005, f"{share:.0%} of the width (max {limit:.0%})"))
    return checks


def failed(checks: list[Check]) -> list[Check]:
    return [c for c in checks if not c.passed]
