"""Photo reader: looks at one uploaded brand photo, once, and describes it for the design step.

What it's for: knowing what's in the photo (so the right photo is picked for a post), its colours, and
where there's calm space for text (so text never covers the product). It is NOT a source of facts about
the product: what the product does or contains still comes only from the brand's approved claims.
"""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

from buildsocial.harness.agent import Agent
from buildsocial.harness.llm.types import ImageInput

PROMPTS_DIR = Path(__file__).parent / "prompts"

Hex = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
Area = Literal["top", "bottom", "left", "right", "center"]
SUMMARY_MAX = 300


class PhotoReading(BaseModel):
    """The photo reader's output. Field order: look first (what's there), then decide (kind, product)."""

    summary: str = Field(description="What is visible, in 1-2 plain sentences, as a person would describe it")
    subjects: list[str] = Field(description="The main things visible, e.g. 'glass of iced tea', 'wooden table'")
    visible_text: list[str] = Field(description="Any words visible in the photo (labels, signs), copied exactly; "
                                                "empty if none")
    other_brands: list[str] = Field(description="Names or logos of brands OTHER than this brand visible in the "
                                                "photo; empty if none (this brand's own logo doesn't count)")
    colors: list[Hex] = Field(description="2 to 5 dominant colours as '#rrggbb', most dominant first")
    background: Literal["plain", "scene", "transparent"] = Field(
        description="plain: studio or flat background; scene: a real setting; transparent: no background")
    lighting: str = Field(description="Light and mood in a few words, e.g. 'soft warm daylight from the left'")
    text_space: list[Area] = Field(description="Areas calm and empty enough to place text without covering the "
                                               "main subject; empty if none")
    issues: list[str] = Field(description="Problems for using it in a post: blurry, too dark, low resolution, "
                                          "watermark, people's faces; empty if none")
    suggested_kind: Literal["product", "lifestyle", "place", "logo", "other"]
    suggested_product: str | None = Field(
        description="The brand product shown, copied exactly from the list; null if none or unsure")


class PhotoReadInput(BaseModel):
    brand_name: str
    products: list[str]  # the brand's product names
    image: ImageInput  # its bytes never appear in traces (see ImageInput)


class PhotoReaderAgent(Agent[PhotoReadInput, PhotoReading]):
    name = "photo_reader"
    output_type = PhotoReading
    system_prompt = (PROMPTS_DIR / "photo_reader.md").read_text()
    max_output_tokens = 1500

    def build_user_message(self, input: PhotoReadInput) -> str:
        products = "\n".join(f"- {p}" for p in input.products) or "- (none listed)"
        return f"Brand: {input.brand_name}\n\nThe brand's products:\n{products}\n\nDescribe the attached photo."

    def build_images(self, input: PhotoReadInput) -> list[ImageInput]:
        return [input.image]

    def check(self, output: PhotoReading, input: PhotoReadInput) -> list[str]:
        errors = []
        if not output.summary.strip():
            errors.append("summary: describe what is visible")
        elif len(output.summary) > SUMMARY_MAX:
            errors.append(f"summary: at most {SUMMARY_MAX} characters (got {len(output.summary)})")
        if not 2 <= len(output.colors) <= 5:
            errors.append(f"colors: give 2 to 5 (got {len(output.colors)})")
        if output.suggested_product is not None and output.suggested_product not in input.products:
            errors.append(f"suggested_product: '{output.suggested_product}' isn't in the list; copy a name exactly "
                          "or use null")
        return errors
