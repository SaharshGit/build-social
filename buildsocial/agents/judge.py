"""Judge agent (evals only): scores a finished post the same way every time.

Its yes/no findings are lists of EXACT quotes from the post, verified in code like the
reviewer's, so the judge can't invent problems. Scores come after their reasons.
"""

from pathlib import Path

from pydantic import BaseModel, Field

from buildsocial.agents.reviewer import quote_in
from buildsocial.harness.agent import Agent
from buildsocial.schemas import BrandProfile, PostBrief, PostDraft, PRODUCTS_HEADING

PROMPTS_DIR = Path(__file__).parent / "prompts"


class Judgement(BaseModel):
    invented_product_claims: list[str] = Field(description="Exact quotes stating unsupported facts about the brand's own product")
    promised_results: list[str] = Field(description="Exact quotes promising or implying forbidden results")
    voice_reason: str
    voice_score: int = Field(ge=1, le=5)
    usefulness_reason: str
    usefulness_score: int = Field(ge=1, le=5)


class JudgeInput(BaseModel):
    brand: BrandProfile
    brief: PostBrief
    draft: PostDraft


class JudgeAgent(Agent[JudgeInput, Judgement]):
    name = "judge"
    output_type = Judgement
    system_prompt = (PROMPTS_DIR / "judge.md").read_text()
    max_output_tokens = 1500

    def build_user_message(self, input: JudgeInput) -> str:
        brand, brief, draft = input.brand, input.brief, input.draft
        donts = "\n".join(f"- {d}" for d in brand.donts) or "- (none)"
        return "\n".join([
            "# Brand profile",
            f"Name: {brand.name}",
            f"What they do: {brand.description.strip()}",
            f"Audience: {brand.audience.strip()}",
            f"Voice: {brand.voice}",
            f"Language: {brand.language}",
            f"Don'ts:\n{donts}",
            "",
            PRODUCTS_HEADING,
            brand.products_text(),
            "",
            "# Brief",
            f"Platform: {brief.platform}",
            f"Topic: {brief.topic}",
            f"Goal: {brief.goal}",
            "",
            "# Post",
            draft.all_text,
        ])

    def check(self, output: Judgement, input: JudgeInput) -> list[str]:
        text = input.draft.all_text
        errors = []
        for field in ("invented_product_claims", "promised_results"):
            for quote in getattr(output, field):
                if not quote.strip() or not quote_in(quote, text):
                    errors.append(f"{field}: '{quote[:60]}' is not in the post; copy exact words or remove it")
        return errors
