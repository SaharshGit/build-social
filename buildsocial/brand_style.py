"""The brand's reference board: read the visual style shared by its reference images, and keep it.

    brands.style = {
        "reading": BrandStyle,     # what the model saw (a person can edit it)
        "asset_ids": [...],        # which reference images it used
        "excluded": [{id, reason}] # references left out (e.g. another brand's logo)
        "kit_mismatch": [...],     # recurring colours far from every kit colour (worth a look)
        "edited": bool,            # a person changed the reading; a re-read won't overwrite it unless asked
        "at", "model_cost", "currency",
    }

Code owns the bookkeeping (which images, counts, staleness, cost); the model only describes the style.
"""

from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.agents.brand_style import MAX_REFERENCES, MIN_REFERENCES, BrandStyle, BrandStyleAgent, BrandStyleInput
from buildsocial.db import assets as asset_ops
from buildsocial.db.models import Asset, AssetKind, Brand, Organization
from buildsocial.harness.llm.types import ImageInput
from buildsocial.harness.storage import Storage
from buildsocial.images import for_model
from buildsocial.photo_reading import ModelDeps
from buildsocial.pipeline import StepRunner
from buildsocial.schemas import BrandProfile

# A recurring colour this far (CIE76 delta E) from every kit colour is clearly a different colour.
KIT_MISMATCH_DELTA_E = 25


class NotEnoughReferences(ValueError):
    pass


class StyleEdited(Exception):
    pass


def _flag(asset: Asset) -> str | None:
    """Why a reference shouldn't shape the brand's style. A photo that was never checked isn't trusted:
    it could show a competitor's look."""
    analysis = asset.analysis or {}
    if "other_brands" not in analysis:  # reading failed, never ran, or predates the check
        return f"not checked yet: read it again (POST /assets/{asset.id}/read)"
    if analysis["other_brands"]:
        return "shows another brand: " + ", ".join(analysis["other_brands"])
    return None


async def references(db: AsyncSession, org: Organization, brand: Brand) -> tuple[list[Asset], list[dict]]:
    """(usable, excluded): the newest library images of kind 'reference', minus flagged ones."""
    usable, excluded = [], []
    for a in await asset_ops.list_assets(db, org, brand.id, in_library=True):
        if a.kind != AssetKind.REFERENCE:
            continue
        if reason := _flag(a):
            excluded.append({"id": str(a.id), "reason": reason})
        elif len(usable) < MAX_REFERENCES:
            usable.append(a)
    return usable, excluded


async def read_style(db: AsyncSession, store: Storage, org: Organization, brand: Brand, deps: ModelDeps,
                     replace_edited: bool = False) -> dict:
    if brand.style and brand.style.get("edited") and not replace_edited:
        raise StyleEdited("the style was edited by a person; read again with replace=true to overwrite it")
    usable, excluded = await references(db, org, brand)
    if len(usable) < MIN_REFERENCES:
        raise NotEnoughReferences(f"need at least {MIN_REFERENCES} reference images (kind 'reference'), "
                                  f"have {len(usable)} usable")

    profile = BrandProfile.model_validate(brand.profile)
    kit = [c.hex for c in profile.design.colors] if profile.design else []
    images, used = [], []
    for a in usable:  # one at a time: never several full-size images in memory
        try:
            data, mime = for_model(store.read_bytes(a.key), max_side=1024)
        except FileNotFoundError:
            excluded.append({"id": str(a.id), "reason": "the image file is missing"})
            continue
        images.append(ImageInput(mime_type=mime, data=data, resolution="medium"))
        used.append(a)
    if len(used) < MIN_REFERENCES:
        raise NotEnoughReferences(f"need at least {MIN_REFERENCES} reference images, {len(used)} could be read")

    tracer = deps.tracer_for(f"style_{str(brand.id)[:8]}")
    run = StepRunner(tracer, deps.client_for, deps.pricing_for)
    try:
        reading = await run(BrandStyleAgent(), BrandStyleInput(brand_name=profile.name, kit_colors=kit, images=images))
    finally:
        tracer.close()

    step = run.steps[0]
    brand.style = {
        "reading": reading.model_dump(mode="json"),
        "asset_ids": [str(a.id) for a in used],
        "excluded": excluded,
        "kit_mismatch": kit_mismatch(reading.palette, kit),
        "edited": False,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model_cost": round(step.cost, 6),
        "currency": step.pricing.currency,
    }
    await db.flush()
    return brand.style


async def style_view(db: AsyncSession, org: Organization, brand: Brand) -> dict:
    """The saved style plus live facts: is it stale, how many references are usable now."""
    usable, excluded = await references(db, org, brand)
    style = brand.style or {}
    current = sorted(str(a.id) for a in usable)
    return {
        "style": style or None,
        "references_usable": len(usable),
        "references_excluded": excluded,
        "stale": bool(style) and sorted(style.get("asset_ids", [])) != current,
    }


def edit_style(brand: Brand, changes: dict) -> dict:
    """A person's correction: merged into the reading, validated, and protected from re-reads."""
    if not brand.style:
        raise ValueError("there is no style to edit yet; read it first")
    reading = BrandStyle.model_validate(brand.style["reading"] | changes)  # raises if invalid
    brand.style = brand.style | {"reading": reading.model_dump(mode="json"), "edited": True}
    return brand.style


# --- colour distance --------------------------------------------------------------------


def kit_mismatch(palette: list[str], kit: list[str]) -> list[str]:
    """Recurring colours (the first 3 of the palette) that are far from every kit colour."""
    if not kit:
        return []
    kit_lab = [_lab(c) for c in kit]
    return [c for c in palette[:3] if min(_delta_e(_lab(c), k) for k in kit_lab) > KIT_MISMATCH_DELTA_E]


def _lab(hex_color: str) -> tuple[float, float, float]:
    """sRGB '#rrggbb' -> CIE L*a*b* (D65), where distance roughly matches how different colours look."""
    def lin(v: int) -> float:
        c = v / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
    r, g, b = (lin(int(hex_color[i:i + 2], 16)) for i in (1, 3, 5))
    x = (0.4124 * r + 0.3576 * g + 0.1805 * b) / 0.95047
    y = 0.2126 * r + 0.7152 * g + 0.0722 * b
    z = (0.0193 * r + 0.1192 * g + 0.9505 * b) / 1.08883

    def f(t: float) -> float:
        return t ** (1 / 3) if t > 0.008856 else 7.787 * t + 16 / 116
    fx, fy, fz = f(x), f(y), f(z)
    return 116 * fy - 16, 500 * (fx - fy), 200 * (fy - fz)


def _delta_e(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
    return sum((p - q) ** 2 for p, q in zip(a, b)) ** 0.5
