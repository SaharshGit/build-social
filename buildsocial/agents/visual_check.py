"""Visual check: a model looks at the finished image and judges it against the brand (skills/design/review.md).

Code has already measured sizes, safe box and contrast. This agent judges what code can't: does it look like the
brand, is there one clear focus, is the product shown truthfully, does it look generic. It also reads the words
off the image; code compares that reading with the approved words (a real read-back, not an assumption).
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from buildsocial.harness.agent import Agent
from buildsocial.harness.llm.types import ImageInput
from buildsocial.harness.skills import render_skills

PROMPTS_DIR = Path(__file__).parent / "prompts"
SKILLS_DIR = Path(__file__).parent.parent / "skills"

CheckName = Literal["words_exact", "text_readable", "one_focus", "brand_colours_fonts", "style_match",
                    "product_true", "not_generic", "no_other_brands", "logo_ok"]
CHECK_NAMES: tuple[str, ...] = CheckName.__args__  # type: ignore[attr-defined]


class VisualItem(BaseModel):
    name: CheckName
    reason: str = Field(description="What you see that decides this check, in one sentence")
    passed: bool


class VisualVerdict(BaseModel):
    """Field order: read the words first, then judge each check (reason before verdict)."""

    headline_as_read: str = Field(description="The main words on the image, exactly as you read them")
    subline_as_read: str | None = Field(description="The smaller supporting line as you read it; null if there is none")
    checks: list[VisualItem] = Field(description="Exactly one entry for each of the nine checks")


class VisualCheckInput(BaseModel):
    brand_name: str
    kit: list[str]            # e.g. "accent: Chai #C9772F"
    fonts: list[str]
    art_direction: str = ""
    style: dict | None = None  # the style board reading, if the brand has one
    headline: str
    subline: str | None = None
    layout: str
    logo_kind: Literal["logo", "wordmark"] = "wordmark"
    uses_photo: bool = False
    image: ImageInput          # its bytes never reach traces


class VisualCheckAgent(Agent[VisualCheckInput, VisualVerdict]):
    name = "visual_check"
    output_type = VisualVerdict
    system_prompt = (PROMPTS_DIR / "visual_check.md").read_text()
    max_output_tokens = 1800

    def build_system_prompt(self, input: VisualCheckInput) -> str:
        skills = render_skills(SKILLS_DIR, ["design/review", "design/anti_generic"])
        return f"{self.system_prompt.strip()}\n\n{skills}"

    def build_user_message(self, input: VisualCheckInput) -> str:
        s = input.style or {}
        lines = [
            f"# Brand: {input.brand_name}",
            "Kit colours: " + "; ".join(input.kit),
            "Fonts: " + "; ".join(input.fonts),
            f"Art direction: {input.art_direction or '(none)'}",
        ]
        if s:
            lines += [f"Style board: lighting {s.get('lighting', '')}; composition {s.get('composition', '')}; "
                      f"mood {', '.join(s.get('mood', []))}; avoid {', '.join(s.get('avoid', []))}"]
        lines += ["", "# Approved words on the image (read-only)", f"Headline: {input.headline}"]
        if input.subline:
            lines.append(f"Subline: {input.subline}")
        lines += ["", f"Layout: {input.layout}",
                  "Logo: " + ("the brand's logo image" if input.logo_kind == "logo"
                              else f"the brand name '{input.brand_name}' written in the heading font (no logo uploaded yet)"),
                  f"A real photo is used: {'yes' if input.uses_photo else 'no (a colour field): product_true and no_other_brands pass'}",
                  "", "The image is attached. Judge it."]
        return "\n".join(lines)

    def build_images(self, input: VisualCheckInput) -> list[ImageInput]:
        return [input.image]

    def check(self, output: VisualVerdict, input: VisualCheckInput) -> list[str]:
        names = [c.name for c in output.checks]
        errors = []
        missing = [n for n in CHECK_NAMES if n not in names]
        if missing:
            errors.append(f"checks: missing {missing}; give exactly one entry for each of the nine checks")
        dupes = sorted({n for n in names if names.count(n) > 1})
        if dupes:
            errors.append(f"checks: {dupes} appear more than once")
        if any(not c.reason.strip() for c in output.checks):
            errors.append("checks: every check needs a reason")
        return errors
