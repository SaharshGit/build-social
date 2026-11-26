"""Brand style reader: looks at a brand's reference images (its best past posts and mood photos) together
and writes down the visual style they share, as structured, checkable notes.

Who reads the result: only the design step and the visual check. Never the writer, reviewer or judge:
reference posts can show prices, offers or claims, and those are not approved facts.
"""

from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from buildsocial.harness.agent import Agent
from buildsocial.harness.llm.types import ImageInput

PROMPTS_DIR = Path(__file__).parent / "prompts"

Hex = Annotated[str, StringConstraints(pattern=r"^#[0-9a-fA-F]{6}$")]
MIN_REFERENCES, MAX_REFERENCES = 3, 15


class BrandStyle(BaseModel):
    """What the reference images share. Field order: look at each image first, then summarise."""

    per_image: list[str] = Field(description="One short line per image, in the order given: what it shows and how "
                                             "it looks (light, colours, composition)")
    palette: list[Hex] = Field(description="3 to 6 colours that recur across the images, '#rrggbb', most used first")
    color_use: str = Field(description="How colour is used, e.g. 'warm earthy tones, one orange accent, lots of cream'")
    lighting: str = Field(description="The usual light, e.g. 'soft natural daylight from a window, gentle shadows'")
    composition: str = Field(description="Framing and crops, e.g. 'close-up, product off-centre, lots of empty space'")
    textures_props: list[str] = Field(description="Surfaces and props that recur, e.g. 'wooden table', 'steel tumbler'")
    text_on_image: str = Field(description="How words appear on the images (size, weight, case, placement); "
                                           "'no text on the images' if none")
    motifs: list[str] = Field(description="Other recurring visual elements, e.g. 'hand holding the cup'; may be empty")
    mood: list[str] = Field(description="3 to 6 words for the overall feel, e.g. 'cosy', 'playful'")
    avoid: list[str] = Field(description="Looks that would clash with this style, based on what the images show "
                                         "(e.g. 'neon colours', 'busy collage layouts')")
    summary: str = Field(description="2-3 sentences a designer can follow to make a new image in this style")


class BrandStyleInput(BaseModel):
    brand_name: str
    kit_colors: list[str] = []  # the brand's own kit colours, if it has a kit (for context only)
    images: list[ImageInput]  # bytes never reach traces (see ImageInput)


class BrandStyleAgent(Agent[BrandStyleInput, BrandStyle]):
    name = "brand_style"
    output_type = BrandStyle
    system_prompt = (PROMPTS_DIR / "brand_style.md").read_text()
    max_output_tokens = 3000

    def build_user_message(self, input: BrandStyleInput) -> str:
        kit = ", ".join(input.kit_colors) or "(no kit yet)"
        return (f"Brand: {input.brand_name}\nThe brand's kit colours: {kit}\n\n"
                f"{len(input.images)} reference images are attached, numbered 1 to {len(input.images)} in order. "
                "Describe the visual style they share.")

    def build_images(self, input: BrandStyleInput) -> list[ImageInput]:
        return input.images

    def check(self, output: BrandStyle, input: BrandStyleInput) -> list[str]:
        n, errors = len(input.images), []
        if len(output.per_image) != n:
            errors.append(f"per_image: write exactly one line per image ({n}), got {len(output.per_image)}")
        if not 3 <= len(output.palette) <= 6:
            errors.append(f"palette: give 3 to 6 colours (got {len(output.palette)})")
        if not 3 <= len(output.mood) <= 6:
            errors.append(f"mood: give 3 to 6 words (got {len(output.mood)})")
        if not output.summary.strip() or len(output.summary) > 600:
            errors.append("summary: 2-3 sentences, at most 600 characters")
        return errors
