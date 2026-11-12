"""Content agent: brand profile + brief -> one platform-native post draft."""

import re
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel

from buildsocial.harness.agent import Agent
from buildsocial.schemas import BrandProfile, PostBrief, PostDraft

PROMPTS_DIR = Path(__file__).parent / "prompts"


@dataclass(frozen=True)
class PlatformRules:
    hook_max: int  # characters visible before the platform's "...more" cut-off
    post_max: int  # platform's hard limit for the whole post
    hashtags_min: int
    hashtags_max: int


PLATFORM_RULES: dict[str, PlatformRules] = {
    "instagram": PlatformRules(hook_max=125, post_max=2200, hashtags_min=3, hashtags_max=10),
    "linkedin": PlatformRules(hook_max=200, post_max=3000, hashtags_min=0, hashtags_max=5),
}


class ContentInput(BaseModel):
    brand: BrandProfile
    brief: PostBrief


class ContentAgent(Agent[ContentInput, PostDraft]):
    name = "content"
    output_type = PostDraft
    system_prompt = (PROMPTS_DIR / "content.md").read_text()

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
            _bullets("Example posts (match the style, don't copy)", brand.example_posts),
            "",
            "# Brief",
            f"Platform: {brief.platform}",
            f"Topic: {brief.topic}",
            f"Goal: {brief.goal}",
            f"Notes: {brief.notes}" if brief.notes else None,
            "",
            f"# {brief.platform.capitalize()} limits",
            f"- Hook: at most {rules.hook_max} characters",
            f"- Whole post (hook + body + call to action + hashtags): at most {rules.post_max} characters",
            f"- Hashtags: {rules.hashtags_min} to {rules.hashtags_max}",
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
        if not rules.hashtags_min <= n <= rules.hashtags_max:
            errors.append(f"hashtags: need {rules.hashtags_min} to {rules.hashtags_max} (got {n})")
        for tag in output.hashtags:
            if not tag.startswith("#") or len(tag) < 2 or any(c.isspace() for c in tag):
                errors.append(f"hashtags: '{tag}' must start with '#' and contain no spaces")

        # Each part of the post has its own field; the body must not duplicate them (seen in Step 2e).
        if _HASHTAG.search(output.body):
            errors.append("body: must not contain hashtags; put them only in the hashtags field")
        cta = _normalize(output.call_to_action)
        if cta and cta in _normalize(output.body):
            errors.append("body: must not repeat the call to action; it is added after the body automatically")

        return errors


_HASHTAG = re.compile(r"(?<!\w)#\w")  # '#word' not preceded by a letter (so 'C#' or URLs#x don't count)


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace/trailing punctuation, for 'is this repeated?' checks."""
    return " ".join(text.lower().split()).strip(" .!?")


def _bullets(title: str, items: list[str]) -> str | None:
    if not items:
        return None
    return f"{title}:\n" + "\n".join(f"- {item.strip()}" for item in items)
