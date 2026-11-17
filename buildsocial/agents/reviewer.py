"""Reviewer agent: checks a draft against the brand, the brief, the plan and the approved facts.

It targets what code rules can't catch: invented product claims, softly worded
promises, a second call to action in the body, copied example lines.

A reviewer can invent problems too, so check() requires every issue to QUOTE the
draft exactly (verified by code), and the verdict must agree with the issue list.
"""

import re
from pathlib import Path

from pydantic import BaseModel, model_validator

from buildsocial.harness.agent import Agent
from buildsocial.schemas import BrandProfile, ContentPlan, PostBrief, PostDraft, ResearchReport, Review

PROMPTS_DIR = Path(__file__).parent / "prompts"
MAX_ISSUES = 8


class ReviewInput(BaseModel):
    brand: BrandProfile
    brief: PostBrief
    draft: PostDraft
    plan: ContentPlan | None = None
    research: ResearchReport | None = None  # resolves the plan's approved facts

    @model_validator(mode="after")
    def _plan_needs_research(self) -> "ReviewInput":
        if self.plan and self.plan.finding_ids and self.research is None:
            raise ValueError("a plan that uses findings needs the research report too")
        return self


class ReviewerAgent(Agent[ReviewInput, Review]):
    name = "reviewer"
    output_type = Review
    system_prompt = (PROMPTS_DIR / "reviewer.md").read_text()
    max_output_tokens = 2000

    def build_user_message(self, input: ReviewInput) -> str:
        brand, brief, draft, plan = input.brand, input.brief, input.draft, input.plan
        sections = [
            "# Brand profile",
            f"Name: {brand.name}",
            f"What they do: {brand.description.strip()}",
            f"Voice: {brand.voice}",
            f"Language: {brand.language}",
            _bullets("Don'ts", brand.donts),
            _bullets("Example posts (wording must not be copied)", brand.example_posts),
            "",
            "# Brief",
            f"Platform: {brief.platform}",
            f"Topic: {brief.topic}",
            f"Goal: {brief.goal}",
            f"Notes: {brief.notes}" if brief.notes else None,
        ]
        if plan is not None:
            facts = plan.approved_facts(input.research) if input.research else []
            sections += [
                "",
                "# Plan",
                f"Angle: {plan.angle}",
                f"Key message: {plan.key_message}",
                f"Call to action direction: {plan.cta_direction}",
                "",
                "# Approved facts (the ONLY researched facts the draft may use)",
                "\n".join(f"- {f.fact}" for f in facts) or "- (none)",
            ]
        sections += [
            "",
            "# Draft to review",
            f"HOOK: {draft.hook}",
            f"BODY: {draft.body}",
            f"CALL TO ACTION: {draft.call_to_action}",
            f"HASHTAGS: {' '.join(draft.hashtags)}",
        ]
        return "\n".join(s for s in sections if s is not None).strip()

    def check(self, output: Review, input: ReviewInput) -> list[str]:
        errors = []
        if output.verdict == "pass" and output.issues:
            errors.append("verdict: is 'pass' but issues were listed; use 'revise', or remove issues that aren't real")
        if output.verdict == "revise" and not output.issues:
            errors.append("verdict: is 'revise' but no issues were listed; list them, or use 'pass'")
        if len(output.issues) > MAX_ISSUES:
            errors.append(f"issues: at most {MAX_ISSUES}; keep the most important")

        draft_text = _normalize(input.draft.full_text)
        for i, issue in enumerate(output.issues, 1):
            if not issue.quote.strip():
                errors.append(f"issue {i}: quote is empty; copy the problem words from the draft")
            elif _normalize(issue.quote) not in draft_text:
                errors.append(
                    f"issue {i}: quote '{issue.quote[:60]}' is not in the draft; copy the exact words "
                    "character for character, or remove the issue if the problem isn't really there"
                )
        return errors


def quote_in(quote: str, text: str) -> bool:
    """Is this quote (ignoring case and spacing differences) in the text?"""
    return _normalize(quote) in _normalize(text)


def _normalize(text: str) -> str:
    # Ignore case and whitespace differences; models often re-wrap lines when quoting.
    return re.sub(r"\s+", " ", text).strip().lower()


def _bullets(title: str, items: list[str]) -> str | None:
    if not items:
        return None
    return f"{title}:\n" + "\n".join(f"- {item.strip()}" for item in items)
