"""Design numbers: the ONE place for canvas sizes, safe areas and type/contrast limits.

The renderer, the visual check and the design step's prompt all read these, so they can't disagree.
Design skills (skills/design/*.md) state principles only; the numbers come from here.
Caption limits (characters, hashtags) are a different thing: see PLATFORM_RULES in agents/content.py.

Sizes checked 2026-11-26 against current platform guides:
- Instagram feed is 1080x1350 (4:5), but the profile grid crops every post to 3:4, so everything that
  matters must sit inside the centred 1012 px-wide strip.
- LinkedIn shows 1080x1350 portrait images in the feed; one canvas for both platforms means one template
  set per brand. (Desktop cropping on LinkedIn: verify on a real render.)
Stories and reels (1080x1920) come with the reel format (V3).
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Box:
    left: int
    top: int
    right: int
    bottom: int

    @property
    def width(self) -> int:
        return self.right - self.left

    @property
    def height(self) -> int:
        return self.bottom - self.top

    def contains(self, other: "Box") -> bool:
        return (self.left <= other.left and self.top <= other.top
                and other.right <= self.right and other.bottom <= self.bottom)


@dataclass(frozen=True)
class CanvasSpec:
    width: int
    height: int
    safe: Box  # text and logo must sit inside this box
    note: str = ""

    @property
    def box(self) -> Box:
        return Box(0, 0, self.width, self.height)


MARGIN = 64  # space between the safe box and anything that crops the image

# Instagram's 3:4 profile-grid crop of a 1080x1350 post: centred, full height.
IG_GRID_CROP = Box(left=round((1080 - 1350 * 3 / 4) / 2), top=0, right=1080 - round((1080 - 1350 * 3 / 4) / 2),
                   bottom=1350)

CANVAS: dict[tuple[str, str], CanvasSpec] = {
    ("instagram", "single_image"): CanvasSpec(
        1080, 1350,
        safe=Box(IG_GRID_CROP.left + MARGIN, MARGIN, IG_GRID_CROP.right - MARGIN, 1350 - MARGIN),
        note="4:5 feed post; the profile grid shows only the centred 3:4 strip"),
    ("linkedin", "single_image"): CanvasSpec(
        1080, 1350, safe=Box(MARGIN, MARGIN, 1080 - MARGIN, 1350 - MARGIN), note="4:5 portrait feed image"),
}


@dataclass(frozen=True)
class TypeSpec:
    """Text sizes in px on a 1080 px-wide canvas (a phone shows it about 2.8x smaller)."""

    min_text_px: int = 32           # nothing smaller: about 11 pt on a phone
    headline_px: tuple[int, int] = (72, 140)
    subline_px: tuple[int, int] = (36, 56)
    max_headline_lines: int = 3
    max_text_sizes: int = 3          # headline, subline, small print: no more levels than that
    max_typefaces: int = 2           # the brand's heading and body fonts only
    # WCAG contrast of text against what is actually behind it (colour, or the photo's pixels):
    contrast_large: float = 3.0      # headline-size text (>= large_text_px)
    contrast_normal: float = 4.5     # anything smaller
    large_text_px: int = 64


TYPE = TypeSpec()


@dataclass(frozen=True)
class LogoSpec:
    width_share: tuple[float, float] = (0.08, 0.14)  # of the canvas width
    wordmark_max_share: float = 0.24  # the brand name in text (no logo yet): can't shrink below min_text_px
    corners: tuple[str, ...] = ("top-left", "top-right", "bottom-left", "bottom-right")


LOGO = LogoSpec()

# Behind text, measured on the rendered pixels (the page with the words hidden):
CONTRAST_PERCENTILE = 10  # judge contrast on the worst 10% of background pixels, not the average
EDGE_LEVEL = 40           # an edge-filter value (0-255) above this is a strong edge (an object's outline, clutter)
MAX_EDGE_SHARE = 0.01     # more than 1% strong edges behind text = busy (plain areas measure 0, a glass 2-3%)

MIN_EMPTY_SHARE = 0.30  # at least 30% of the canvas is calm space (no text, no busy detail)


def canvas_for(platform: str, format: str) -> CanvasSpec:
    try:
        return CANVAS[(platform, format)]
    except KeyError:
        raise ValueError(f"no canvas defined for {format} on {platform}") from None


def specs_text(platform: str, format: str) -> str:
    """The numbers as prompt text, for the design step and the visual check."""
    c, t = canvas_for(platform, format), TYPE
    s = c.safe
    return "\n".join([
        f"# Canvas: {platform} {format}",
        f"- Size: {c.width}x{c.height} px ({c.note})",
        f"- Safe box for all text and the logo: x {s.left}-{s.right}, y {s.top}-{s.bottom}",
        f"- Text sizes: headline {t.headline_px[0]}-{t.headline_px[1]} px (at most {t.max_headline_lines} lines), "
        f"subline {t.subline_px[0]}-{t.subline_px[1]} px, nothing under {t.min_text_px} px; "
        f"at most {t.max_text_sizes} sizes and {t.max_typefaces} typefaces",
        f"- Contrast against what is behind the text: at least {t.contrast_large}:1 for text of "
        f"{t.large_text_px} px or more, {t.contrast_normal}:1 for smaller text",
        f"- Logo: {LOGO.width_share[0]:.0%}-{LOGO.width_share[1]:.0%} of the width, in one corner of the safe box",
        f"- At least {MIN_EMPTY_SHARE:.0%} of the canvas stays calm, empty space",
    ])
