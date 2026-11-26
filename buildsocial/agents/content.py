"""Content agent: brand profile + brief (+ optional strategy plan) -> one platform-native post draft."""

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, model_validator

from buildsocial.feedback_rules import Rule, rule_error
from buildsocial.harness.agent import Agent
from buildsocial.harness.skills import render_skills
from buildsocial.agents.reviewer import quote_in
from buildsocial.schemas import BrandProfile, ContentPlan, PostBrief, PostDraft, ResearchReport, ReviewIssue, PRODUCTS_HEADING

PROMPTS_DIR = Path(__file__).parent / "prompts"
SKILLS_DIR = Path(__file__).parent.parent / "skills"


@dataclass(frozen=True)
class PlatformRules:
    hook_max: int  # characters visible before the platform's "...more" cut-off
    post_max: int  # platform's hard limit for the whole post
    hashtags_min: int
    hashtags_max: int
    banned_phrases: tuple[str, ...] = ()  # conventions that don't exist on this platform


PLATFORM_RULES: dict[str, PlatformRules] = {
    "instagram": PlatformRules(hook_max=125, post_max=2200, hashtags_min=3, hashtags_max=10),
    # "link in bio" is an Instagram convention; LinkedIn posts have no bio link (seen 2026-11-17).
    "linkedin": PlatformRules(hook_max=200, post_max=3000, hashtags_min=0, hashtags_max=5,
                              banned_phrases=("link in bio", "link in the bio")),
}


# Quotes shorter than this aren't required to disappear in a revision: a short flagged
# phrase (often a product name) may legitimately stay while its claim is reworded.
MIN_CHECKED_QUOTE = 15


class Revision(BaseModel):
    """A draft to rewrite: keep what works, fix every issue and apply the person's feedback."""

    previous: PostDraft
    issues: list[ReviewIssue] = []  # from the Reviewer agent
    human_feedback: str | None = None  # from the person approving the post

    @model_validator(mode="after")
    def _needs_something_to_fix(self) -> "Revision":
        if not self.issues and not (self.human_feedback or "").strip():
            raise ValueError("a revision needs reviewer issues or human feedback")
        return self


class ContentInput(BaseModel):
    brand: BrandProfile
    brief: PostBrief
    # Optional: from the Strategy agent. Without a plan the agent writes from the brief alone.
    plan: ContentPlan | None = None
    research: ResearchReport | None = None  # needed to resolve the plan's finding numbers
    revision: Revision | None = None  # set when rewriting after a review
    # Measurable requests from the person (e.g. at most 400 characters), enforced in check(): they win over
    # the platform defaults (e.g. "no hashtags" beats Instagram's usual minimum).
    person_rules: list[Rule] = []

    @model_validator(mode="after")
    def _plan_needs_research(self) -> "ContentInput":
        if self.plan and self.plan.finding_ids and self.research is None:
            raise ValueError("a plan that uses findings needs the research report too")
        return self


class ContentAgent(Agent[ContentInput, PostDraft]):
    name = "content"
    output_type = PostDraft
    system_prompt = (PROMPTS_DIR / "content.md").read_text()
    max_output_tokens = 2500  # longest post (LinkedIn, 3000 chars) + other fields, with room to spare

    def build_system_prompt(self, input: ContentInput) -> str:
        # Base prompt first (identical on every call, so the provider can cache it),
        # then only the skills this brief needs.
        brief = input.brief
        skills = render_skills(SKILLS_DIR, [f"platforms/{brief.platform}", f"formats/{brief.format}"])
        return f"{self.system_prompt.strip()}\n\n{skills}"

    def build_user_message(self, input: ContentInput) -> str:
        brand, brief = input.brand, input.brief
        rules = PLATFORM_RULES[brief.platform]

        sections = [
            "# Brand",
            f"Name: {brand.name}",
            f"What they do: {brand.description.strip()}",
            f"Audience: {brand.audience.strip()}",
            f"Voice: {brand.voice}",
            f"Language: {brand.language}",
            _bullets("Content pillars", brand.content_pillars),
            _bullets("Do", brand.dos),
            _bullets("Don't", brand.donts),
            "",
            PRODUCTS_HEADING,
            brand.products_text(),
            _bullets("Example posts (match the style, don't copy)", brand.example_posts),
            "",
            "# Brief",
            f"Platform: {brief.platform}",
            f"Format: {brief.format}",
            f"Topic: {brief.topic}",
            f"Goal: {brief.goal}",
            f"Notes: {brief.notes}" if brief.notes else None,
            _plan_section(input),
            "",
            f"# {brief.platform.capitalize()} limits",
            f"- Hook: at most {rules.hook_max} characters",
            f"- Whole post (hook + body + call to action + hashtags): at most {rules.post_max} characters",
            f"- Hashtags: {rules.hashtags_min} to {rules.hashtags_max}",
            _revision_section(input),
        ]
        return "\n".join(s for s in sections if s is not None).strip()

    def check(self, output: PostDraft, input: ContentInput) -> list[str]:
        """Platform rules, enforced in code: the LLM is told them but can't be trusted to count."""
        platform = input.brief.platform
        rules = PLATFORM_RULES[platform]
        errors = []

        if output.platform != platform:
            errors.append(f"platform: must be '{platform}' (got '{output.platform}')")
        if len(output.hook) > rules.hook_max:
            errors.append(f"hook: must be at most {rules.hook_max} characters (got {len(output.hook)}); shorten it")
        if len(output.full_text) > rules.post_max:
            errors.append(f"post: whole post must be at most {rules.post_max} characters (got {len(output.full_text)})")

        n = len(output.hashtags)
        hashtags_min = rules.hashtags_min
        for r in input.person_rules:  # the person's limit wins over the platform's minimum
            if r.kind == "no_hashtags":
                hashtags_min = 0
            elif r.kind == "max_hashtags":
                hashtags_min = min(hashtags_min, int(r.value or 0))
        if not hashtags_min <= n <= rules.hashtags_max:
            errors.append(f"hashtags: need {hashtags_min} to {rules.hashtags_max} (got {n})")
        for tag in output.hashtags:
            if not tag.startswith("#") or len(tag) < 2 or any(c.isspace() for c in tag):
                errors.append(f"hashtags: '{tag}' must start with '#' and contain no spaces")

        # Each part of the post has its own field; the body must not duplicate them (seen in Step 2e).
        if _HASHTAG.search(output.body):
            errors.append("body: must not contain hashtags; put them only in the hashtags field")
        errors += [f"request: {e}" for r in input.person_rules if (e := rule_error(r, output))]

        cta = _normalize(output.call_to_action)
        if cta and cta in _normalize(output.body):
            errors.append("body: must not repeat the call to action; it is added after the body automatically")

        # If the brief is about one of the brand's listed products, the post must name it
        # (a revision once removed the product entirely while fixing another sentence).
        for product in input.brand.products:
            name = product.name.lower()
            if name in input.brief.topic.lower() and name not in output.full_text.lower():
                errors.append(f"post: the brief is about '{product.name}', so the post must name it")

        for phrase in rules.banned_phrases:
            if phrase in output.full_text.lower():
                errors.append(f"post: '{phrase}' doesn't exist on {platform}; rewrite that part for {platform}")

        # A revision must actually remove what the reviewer flagged.
        if input.revision:
            for issue in input.revision.issues:
                if len(issue.quote.strip()) >= MIN_CHECKED_QUOTE and quote_in(issue.quote, output.full_text):
                    errors.append(
                        f"revision: '{issue.quote[:60]}' was flagged ({issue.type}) and is still in the draft; "
                        f"{issue.fix}"
                    )

        return errors


_HASHTAG = re.compile(r"(?<!\w)#\w")  # '#word' not preceded by a letter (so 'C#' or URLs#x don't count)


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace/trailing punctuation, for 'is this repeated?' checks."""
    return " ".join(text.lower().split()).strip(" .!?")


def _revision_section(input: ContentInput) -> str | None:
    rev = input.revision
    if rev is None:
        return None
    lines = [
        "",
        "# Revise your previous draft",
        "Rewrite your previous draft so that EVERY point below is addressed. "
        "Keep everything else that works (angle, key message, voice, structure). "
        "The brand rules still apply to every change. When you remove a problem sentence, keep the post about the "
        "brief's topic: it must still clearly name the product or subject.",
        "",
        "Previous draft:",
        f"HOOK: {rev.previous.hook}",
        f"BODY: {rev.previous.body}",
        f"CALL TO ACTION: {rev.previous.call_to_action}",
        f"HASHTAGS: {' '.join(rev.previous.hashtags)}",
    ]
    if rev.human_feedback:
        lines += ["", "Feedback from the person approving this post (most important):", rev.human_feedback.strip()]
    if rev.issues:
        lines += ["", "Issues found by the editor:"]
        lines += [
            f'{i}. [{issue.type}] "{issue.quote}": {issue.problem} Fix: {issue.fix}'
            for i, issue in enumerate(rev.issues, 1)
        ]
    return "\n".join(lines)


def _plan_section(input: ContentInput) -> str | None:
    plan = input.plan
    if plan is None:
        return None
    facts = plan.approved_facts(input.research) if input.research else []
    lines = [
        "",
        "# Plan (from the strategist: follow it)",
        f"Angle: {plan.angle}",
        f"Key message: {plan.key_message}",
        f"Call to action direction: {plan.cta_direction}",
        "Facts you may use (don't exaggerate or add to them, and phrase them within the brand rules; these, plus the "
        "brand profile and brief, are the ONLY facts allowed):",
    ]
    lines += [f"- {f.tagged()}" for f in facts] or ["- (none: use only the brand profile and brief)"]
    return "\n".join(lines)


def _bullets(title: str, items: list[str]) -> str | None:
    if not items:
        return None
    return f"{title}:\n" + "\n".join(f"- {item.strip()}" for item in items)
