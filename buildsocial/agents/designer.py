"""Design agent: picks how a single-image post looks (layout, photo, where the text goes).

It never writes or changes words: the headline and subline come from the approved draft and are placed by
code. It only chooses among options the brand actually has (its kit, its photos), and code checks every
choice against what those photos and colours allow (check() below), retrying with the reasons.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from buildsocial.design_specs import TYPE, specs_text
from buildsocial.harness.agent import Agent
from buildsocial.harness.skills import render_skills
from buildsocial.render.layouts import Layout, readable_on
from buildsocial.schemas import BrandProfile, ColorRole, PostBrief, contrast_ratio

PROMPTS_DIR = Path(__file__).parent / "prompts"
SKILLS_DIR = Path(__file__).parent.parent / "skills"

# Where to keep the subject in view when the photo is cropped to the layout.
Focus = Literal["center", "top", "bottom", "left", "right"]
FOCUS_CSS: dict[str, str] = {"center": "center center", "top": "center 20%", "bottom": "center 80%",
                             "left": "20% center", "right": "80% center"}


class DesignChoice(BaseModel):
    """The design agent's output. Field order: think of directions first, then decide, then the details."""

    directions: list[str] = Field(
        description="Three different ways this image could look, from the most expected to the least, one short "
                    "sentence each. Then pick the strongest one that still follows the brand's style.")
    rationale: str = Field(description="Why the chosen direction fits this brand, photo and headline, in 1-2 sentences")
    layout: Layout = Field(description="photo: photo fills the image and the headline sits in its calm area; "
                                       "split: photo on top, text panel below; framed: photo inside a colour field; "
                                       "type_led: colour field with the headline as the design")
    photo_id: str | None = Field(description="The chosen photo's id, copied exactly from the list; "
                                             "null only for type_led")
    text_area: Literal["top", "bottom"] | None = Field(
        description="photo layout only: 'top' or 'bottom', one of the photo's calm areas; null otherwise")
    focus: Focus = Field(description="Which part of the photo to keep in view when it is cropped")
    field_role: ColorRole | None = Field(
        description="type_led only: the kit colour role for the colour field (never 'text'); null otherwise")
    scrim: bool = Field(description="photo layout only: true to add a soft kit-colour fade behind the text; "
                                    "false otherwise")


class PhotoOption(BaseModel):
    """One photo the brand has, as the photo reader described it (never a product claim)."""

    id: str
    product: str | None = None
    description: str | None = None
    text_space: list[str] = []      # calm areas where text fits: top, bottom, left, right, center
    background: str | None = None   # plain / scene / transparent
    lighting: str | None = None
    colors: list[str] = []
    issues: list[str] = []
    attached: bool = False          # the person attached it to this post: it must be used


class DesignInput(BaseModel):
    brand: BrandProfile  # must have a design kit
    brief: PostBrief
    headline: str        # the approved words on the image: read only
    subline: str | None = None
    platform: str = "instagram"
    photos: list[PhotoOption] = []
    style: dict | None = None  # the brand's style board reading (BrandStyle fields), if it has one
    # A retry: the layout that just failed and what was wrong with it. The new design must differ.
    avoid_layout: Layout | None = None
    avoid_notes: list[str] = []


class DesignerAgent(Agent[DesignInput, DesignChoice]):
    name = "designer"
    output_type = DesignChoice
    system_prompt = (PROMPTS_DIR / "designer.md").read_text()
    max_output_tokens = 1200

    def build_system_prompt(self, input: DesignInput) -> str:
        skills = render_skills(SKILLS_DIR, ["design/art_direction", "design/layout", "design/anti_generic",
                                            f"design/formats/{input.brief.format}"])
        return f"{self.system_prompt.strip()}\n\n{skills}\n\n{specs_text(input.platform, input.brief.format)}"

    def build_user_message(self, input: DesignInput) -> str:
        d = input.brand.design
        if d is None:
            raise ValueError("the brand has no design kit")
        lines = [
            f"# Brand: {input.brand.name}",
            f"Voice: {input.brand.voice}",
            "",
            "# Brand kit",
            *[f"- {c.role}: {c.name} {c.hex}" for c in d.colors],
            f"- Headline font: {d.fonts.heading.family}; body font: {d.fonts.body.family}",
            f"Art direction: {d.art_direction or '(none)'}",
            *[f"Do: {x}" for x in d.visual_dos],
            *[f"Don't: {x}" for x in d.visual_donts],
            "",
        ]
        s = input.style
        if s:
            lines += ["# Style board (what the brand's own pictures look like)",
                      f"Lighting: {s.get('lighting', '')}", f"Composition: {s.get('composition', '')}",
                      f"Mood: {', '.join(s.get('mood', []))}", f"Avoid: {', '.join(s.get('avoid', []))}", ""]
        lines += ["# This post", f"Topic: {input.brief.topic}", f"Goal: {input.brief.goal}",
                  f"Platform: {input.platform}", "",
                  "# Words on the image (fixed: do not change, only place)",
                  f"Headline: {input.headline}"]
        if input.subline:
            lines.append(f"Subline: {input.subline}")
        if input.avoid_layout:
            lines += ["", f"# The previous design ('{input.avoid_layout}') did not work. Choose a different layout.",
                      *[f"- {n}" for n in input.avoid_notes]]
        lines += ["", "# Photos available (use only these ids)"]
        if not input.photos:
            lines.append("(none: use type_led)")
        for p in input.photos:
            lines += [f"- id {p.id}{' [ATTACHED BY THE PERSON: must be used]' if p.attached else ''}",
                      f"    shows: {p.description or '(not described)'}"
                      + (f" | product: {p.product}" if p.product else ""),
                      f"    calm areas for text: {', '.join(p.text_space) or 'none'}"
                      f" | background: {p.background or '?'} | lighting: {p.lighting or '?'}"
                      + (f" | issues: {', '.join(p.issues)}" if p.issues else "")]
        return "\n".join(lines)

    def check(self, output: DesignChoice, input: DesignInput) -> list[str]:
        errors: list[str] = []
        options = {p.id: p for p in input.photos}
        attached = [p for p in input.photos if p.attached]
        d = input.brand.design

        if input.avoid_layout and output.layout == input.avoid_layout:
            errors.append(f"layout: '{input.avoid_layout}' just failed; choose a different layout")
        if output.layout == "type_led":
            if attached:
                errors.append("layout: the person attached a photo, so the post must use it; "
                              "choose photo, split or framed")
            roles = {c.role for c in d.colors} if d else set()
            if output.field_role is None or output.field_role == "text" or output.field_role not in roles:
                errors.append(f"field_role: for type_led choose one of {sorted(roles - {'text'})}")
            elif d:
                field = d.color(output.field_role)
                need = TYPE.contrast_normal if input.subline else TYPE.contrast_large
                text = readable_on(field.hex, d)
                if contrast_ratio(text, field.hex) < need:
                    errors.append(f"field_role: no kit text colour reads on {output.field_role} ({field.hex}); "
                                  f"choose another colour")
        else:
            if not input.photos:
                errors.append("layout: no photos are available; use type_led")
            elif output.photo_id not in options:
                errors.append(f"photo_id: must be one of {sorted(options)} (copied exactly)")
            elif attached and output.photo_id != attached[0].id:
                errors.append(f"photo_id: use the photo the person attached ({attached[0].id})")
            if output.layout == "photo" and output.photo_id in options:
                usable = [a for a in options[output.photo_id].text_space if a in ("top", "bottom")]
                if not usable:
                    errors.append("layout: this photo has no calm area at the top or bottom for text; "
                                  "choose split or framed (text goes beside the photo, not on it)")
                elif output.text_area not in usable:
                    errors.append(f"text_area: must be one of {usable} (this photo's calm areas)")
        if output.scrim and output.layout != "photo":
            errors.append("scrim: only for the photo layout; set it to false")
        return errors
