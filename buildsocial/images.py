"""Checking and cleaning uploaded images before they are stored.

- The format is read from the file's contents, never from its name or the browser's content type.
- Only PNG, JPEG and WebP. No SVG: an SVG is a document that can carry scripts.
- Size and pixel limits, so a small "decompression bomb" file can't take the server's memory.
- Metadata is removed (phone photos carry the GPS location where they were taken), and the photo is
  rotated the way the phone held it, so later steps see it upright.
"""

from dataclasses import dataclass
from io import BytesIO

from PIL import Image, ImageOps, UnidentifiedImageError

MAX_BYTES = 10 * 1024 * 1024  # 10 MB
MAX_PIXELS = 40_000_000       # e.g. 8000 x 5000; more than any phone photo
MIN_SIDE = 200                # smaller than this is too small to design with

# Pillow format name -> (content type, file extension)
ALLOWED = {"PNG": ("image/png", "png"), "JPEG": ("image/jpeg", "jpg"), "WEBP": ("image/webp", "webp")}


class BadImage(ValueError):
    """The upload isn't an image we accept. The message is safe to show the person."""


@dataclass(frozen=True)
class CleanImage:
    data: bytes
    content_type: str
    ext: str
    width: int
    height: int


def clean_image(data: bytes) -> CleanImage:
    if len(data) > MAX_BYTES:
        raise BadImage(f"the image is larger than {MAX_BYTES // (1024 * 1024)} MB")
    try:
        with Image.open(BytesIO(data)) as probe:  # reads only the header: cheap, even for huge images
            fmt, (w, h) = probe.format, probe.size
            if fmt not in ALLOWED:
                raise BadImage("use a PNG, JPEG or WebP image")
            if w * h > MAX_PIXELS:
                raise BadImage(f"the image is too large ({w}x{h} pixels)")
            probe.verify()  # checks the file isn't broken or truncated
        with Image.open(BytesIO(data)) as im:
            im.load()
            upright = ImageOps.exif_transpose(im)
            return _reencode(upright, fmt)
    except BadImage:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError, ValueError) as e:
        raise BadImage("the file isn't a readable image") from e


def _reencode(im: Image.Image, fmt: str) -> CleanImage:
    w, h = im.size
    if min(w, h) < MIN_SIDE:
        raise BadImage(f"the image is too small ({w}x{h}); use at least {MIN_SIDE} pixels on each side")
    out = BytesIO()
    # Saving a fresh copy without exif/info keeps the pixels and drops the metadata.
    if fmt == "JPEG":
        im.convert("RGB").save(out, "JPEG", quality=95, optimize=True)
    elif fmt == "WEBP":
        im.save(out, "WEBP", quality=95)
    else:
        im.save(out, "PNG", optimize=True)
    content_type, ext = ALLOWED[fmt]
    return CleanImage(out.getvalue(), content_type, ext, w, h)


def for_model(data: bytes, max_side: int = 1024) -> tuple[bytes, str]:
    """A smaller JPEG copy to send to a model: (bytes, content type).

    Models don't need full resolution to judge style, and Gemini caps one request at 20 MB, so a board
    of 15 full-size uploads would fail. One image is decoded at a time; draft() lets JPEGs decode at a
    reduced size, so a 40 MP photo never sits fully in memory.
    """
    with Image.open(BytesIO(data)) as im:
        if im.format == "JPEG":
            im.draft("RGB", (max_side, max_side))
        im = im.convert("RGB")  # model copies don't need transparency
        im.thumbnail((max_side, max_side))
        out = BytesIO()
        im.save(out, "JPEG", quality=85)
    return out.getvalue(), "image/jpeg"
