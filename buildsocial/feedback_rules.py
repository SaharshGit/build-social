"""Measurable feedback, checked by code: "under 400 characters", "max 3 hashtags", "no emojis"...

A person's feedback often contains requests code can verify exactly. These are checked here,
never by a model: the model only has to recognise them (Q2) or the eval case lists them (Q1).
"""

import re
from typing import Literal

from pydantic import BaseModel, Field

from buildsocial.schemas import PostDraft

RuleKind = Literal[
    "max_chars",          # the whole post is at most N characters
    "max_hashtags",       # at most N hashtags
    "no_hashtags",
    "no_emojis",
    "hook_is_question",   # the opening line ends with "?"
    "ends_with_question", # the call to action ends with "?"
    "must_include",       # this phrase appears (case-insensitive)
    "must_not_include",   # this phrase doesn't appear
]


class Rule(BaseModel):
    kind: RuleKind
    value: str | None = Field(default=None, description="N for max_*, the phrase for must_(not_)include")

    def describe(self) -> str:
        return {
            "max_chars": f"at most {self.value} characters",
            "max_hashtags": f"at most {self.value} hashtags",
            "no_hashtags": "no hashtags",
            "no_emojis": "no emojis",
            "hook_is_question": "the hook is a question",
            "ends_with_question": "ends with a question",
            "must_include": f"mentions “{self.value}”",
            "must_not_include": f"doesn't mention “{self.value}”",
        }[self.kind]


# Emoji and pictographs (incl. variation selectors and joiners). Plain symbols like ₹ or © are not emojis.
_EMOJI = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F000-\U0001F02F\U0001F1E6-\U0001F1FF"
    "\uFE0F\u200D\u2B50\u2B06\u2194-\u21AA]"
)


def _ends_with_question(line: str) -> bool:
    """"Baarish wali shaam? ☕" counts: trailing emojis and spaces are ignored."""
    return _EMOJI.sub("", line).rstrip().endswith("?")


def check_rule(rule: Rule, draft: PostDraft) -> tuple[bool, str]:
    """(passed, what was found) for one rule on one draft."""
    text = draft.full_text
    k, v = rule.kind, rule.value
    if k == "max_chars":
        n = len(text)
        return n <= int(v or 0), f"{n} characters"
    if k == "max_hashtags":
        n = len(draft.hashtags)
        return n <= int(v or 0), f"{n} hashtags"
    if k == "no_hashtags":
        return not draft.hashtags and "#" not in text, f"{len(draft.hashtags)} hashtags"
    if k == "no_emojis":
        found = _EMOJI.findall(text)
        return not found, f"emojis: {''.join(found)}" if found else "no emojis"
    if k == "hook_is_question":
        return _ends_with_question(draft.hook), f"hook: “{draft.hook.strip()[:80]}”"
    if k == "ends_with_question":
        return _ends_with_question(draft.call_to_action), f"ends: “{draft.call_to_action.strip()[-80:]}”"
    if k == "must_include":
        return (v or "").lower() in text.lower(), "found" if (v or "").lower() in text.lower() else "not found"
    if k == "must_not_include":
        return (v or "").lower() not in text.lower(), "absent" if (v or "").lower() not in text.lower() else "still there"
    raise ValueError(f"unknown rule {k}")
