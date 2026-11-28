"""The design step around the design agent: which photos are candidates, the agent's choice turned into a
page to render, and a plain fallback if the agent can't be used.

Photo rules (code, not model):
- Photos the person attached to this post come first and must be used.
- Library photos fill the rest: only product / lifestyle / place photos, only ones the photo reader has
  checked, never one showing another brand, never one with problems (blur, faces...) unless attached.
- A photo that shows another brand is never used, even if attached.
"""

import uuid
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.agents.designer import FOCUS_CSS, DesignChoice, DesignInput, PhotoOption
from buildsocial.db import assets as asset_ops
from buildsocial.db.models import Asset, AssetKind, Brand, Organization
from buildsocial.design_specs import canvas_for
from buildsocial.harness.storage import Storage
from buildsocial.images import for_model
from buildsocial.render.layouts import Image, PageSpec
from buildsocial.schemas import BrandProfile, PostBrief, PostDraft

MAX_OPTIONS = 6  # photos the design agent sees
LIBRARY_KINDS = (AssetKind.PRODUCT, AssetKind.LIFESTYLE, AssetKind.PLACE)
PHOTO_PX = 1600  # long side of the photo placed in the image: plenty for a 1080 px canvas


@dataclass
class Candidates:
    assets: list[Asset] = field(default_factory=list)  # in the order the agent sees them
    attached: list[str] = field(default_factory=list)  # ids of the ones the person attached (usable)
    skipped: list[tuple[str, str]] = field(default_factory=list)  # (asset id, why it can't be used)


def _analysis(a: Asset) -> dict:
    return a.analysis or {}


def _unusable(a: Asset) -> str | None:
    """Why a photo can never go in an image, even if the person attached it."""
    an = _analysis(a)
    if "other_brands" not in an:
        return "it hasn't been read yet"
    if an["other_brands"]:
        return "it shows another brand (" + ", ".join(an["other_brands"]) + ")"
    return None


async def candidates(db: AsyncSession, org: Organization, brand: Brand, brief: PostBrief) -> Candidates:
    out = Candidates()
    seen: set[str] = set()
    for photo_id in brief.photo_ids:  # attached first, in the order the person gave them
        try:
            a = await asset_ops.get_asset(db, org, uuid.UUID(photo_id))
        except ValueError:
            a = None
        if a is None or a.brand_id != brand.id:
            out.skipped.append((photo_id, "it isn't one of this brand's photos"))
        elif (why := _unusable(a)):
            out.skipped.append((photo_id, why))
        else:
            out.assets.append(a)
            out.attached.append(str(a.id))
            seen.add(str(a.id))

    topic = brief.topic.lower()
    library = [a for a in await asset_ops.list_assets(db, org, brand.id, in_library=True)
               if a.kind in LIBRARY_KINDS and str(a.id) not in seen and _unusable(a) is None
               and not _analysis(a).get("issues")]
    # Photos of the product the post is about first, then the rest (newest first, as listed).
    library.sort(key=lambda a: 0 if a.product and a.product.lower() in topic else 1)
    for a in library:
        if len(out.assets) >= MAX_OPTIONS:
            break
        out.assets.append(a)
    return out


def option_from(a: Asset, attached: bool) -> PhotoOption:
    an = _analysis(a)
    return PhotoOption(
        id=str(a.id), product=a.product or an.get("suggested_product"), description=a.description,
        text_space=list(an.get("text_space") or []), background=an.get("background"), lighting=an.get("lighting"),
        colors=list(an.get("colors") or []), issues=list(an.get("issues") or []), attached=attached)


def design_input(brand: Brand, brief: PostBrief, draft: PostDraft, cands: Candidates) -> DesignInput:
    reading = (brand.style or {}).get("reading")
    return DesignInput(
        brand=BrandProfile.model_validate(brand.profile), brief=brief,
        headline=draft.image_text.headline if draft.image_text else "",
        subline=draft.image_text.subline if draft.image_text else None,
        platform=brief.platform,
        photos=[option_from(a, str(a.id) in cands.attached) for a in cands.assets], style=reading)


def fallback_choice(inp: DesignInput) -> DesignChoice:
    """A safe design without the agent: the attached or best-matching photo with the text beside it
    (split), or a plain colour field when there is no photo. Never puts text on a photo."""
    d = inp.brand.design
    note = "chosen by rule: the design agent wasn't used"
    if inp.photos:
        photo = next((p for p in inp.photos if p.attached), inp.photos[0])
        return DesignChoice(directions=[note], rationale=note, layout="split", photo_id=photo.id, text_area=None,
                            focus="center", field_role=None, scrim=False)
    role = "accent" if d and d.color("accent") else "background"
    return DesignChoice(directions=[note], rationale=note, layout="type_led", photo_id=None, text_area=None,
                        focus="center", field_role=role, scrim=False)


def _image(store: Storage, a: Asset, max_side: int | None) -> Image:
    data = store.read_bytes(a.key)
    if max_side is None:  # the logo: keep it exactly as uploaded (it may be transparent)
        return Image(data, a.content_type)
    data, mime = for_model(data, max_side=max_side)
    return Image(data, mime)


async def page_spec(db: AsyncSession, store: Storage, org: Organization, brand: Brand, brief: PostBrief,
                    draft: PostDraft, choice: DesignChoice, cands: Candidates) -> PageSpec:
    """The page to render: the approved words, the brand's kit and logo, and the agent's choices."""
    profile = BrandProfile.model_validate(brand.profile)
    if profile.design is None or draft.image_text is None:
        raise ValueError("a design needs the brand's kit and the post's image text")
    photo = None
    if choice.layout != "type_led":
        asset = next((a for a in cands.assets if str(a.id) == choice.photo_id), None)
        if asset is None:
            raise ValueError(f"photo {choice.photo_id} isn't one of the candidates")
        photo = _image(store, asset, PHOTO_PX)
    logo = None
    if profile.design.logo_asset_id:
        logo_asset = await asset_ops.get_asset(db, org, uuid.UUID(profile.design.logo_asset_id))
        if logo_asset is not None:
            logo = _image(store, logo_asset, None)
    return PageSpec(
        layout=choice.layout, headline=draft.image_text.headline, subline=draft.image_text.subline,
        brand_name=profile.name, design=profile.design, canvas=canvas_for(brief.platform, brief.format),
        photo=photo, logo=logo, text_area=choice.text_area or "top", photo_focus=FOCUS_CSS[choice.focus],
        field_role=choice.field_role or "accent", scrim=choice.scrim)
