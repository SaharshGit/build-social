"""Feedback check: was each thing the person asked for actually done in the revised post?

Two uses:
- evals (Q1): the case lists the requests; the agent judges each one.
- the revision flow (Q2): the agent splits free feedback into requests, marks the measurable
  ones as rules (checked by code in feedback_rules, never trusted from the model), and judges the rest.

Every "done" must quote evidence from the new post (verified by check()), so it can't just say yes.
"""

from pathlib import Path

from pydantic import BaseModel, Field

from buildsocial.agents.reviewer import quote_in
from buildsocial.feedback_rules import RuleKind
from buildsocial.harness.agent import Agent

PROMPTS_DIR = Path(__file__).parent / "prompts"


class RequestCheck(BaseModel):
    request: str = Field(description="One thing the person asked for, in their words")
    rule_kind: RuleKind | None = Field(default=None, description="Set when code can check it exactly; else empty")
    rule_value: str | None = Field(default=None, description="N for max_chars / max_hashtags, the phrase for must_(not_)include")
    reason: str = Field(description="Why it is or isn't done, comparing before and after")
    evidence: str = Field(default="", description="Exact words from the NEW post showing it's done; empty if not done or if the request was to remove something")
    done: bool


class FeedbackReport(BaseModel):
    checks: list[RequestCheck]


class FeedbackCheckInput(BaseModel):
    feedback: str
    requests: list[str] = []  # given: judge exactly these; empty: split the feedback yourself
    before: str  # full text of the post before the revision
    after: str   # full text after


class FeedbackCheckAgent(Agent[FeedbackCheckInput, FeedbackReport]):
    name = "feedback_check"
    output_type = FeedbackReport
    system_prompt = (PROMPTS_DIR / "feedback_check.md").read_text()
    max_output_tokens = 1500

    def build_user_message(self, input: FeedbackCheckInput) -> str:
        lines = ["# What the person asked for", input.feedback, ""]
        if input.requests:
            lines += ["# Check exactly these requests, in this order", *[f"{i}. {r}" for i, r in enumerate(input.requests, 1)], ""]
        lines += ["# Post BEFORE the revision", input.before, "", "# Post AFTER the revision", input.after]
        return "\n".join(lines)

    def check(self, output: FeedbackReport, input: FeedbackCheckInput) -> list[str]:
        errors = []
        if input.requests and len(output.checks) != len(input.requests):
            errors.append(f"checks: give exactly {len(input.requests)} checks, one per listed request, in order")
        if not output.checks:
            errors.append("checks: list at least one request")
        for i, c in enumerate(output.checks, 1):
            if c.evidence.strip() and not quote_in(c.evidence, input.after):
                errors.append(f"checks[{i}].evidence: '{c.evidence[:60]}' is not in the AFTER post; copy exact words or leave it empty")
            if c.rule_kind in ("max_chars", "max_hashtags") and not (c.rule_value or "").strip().isdigit():
                errors.append(f"checks[{i}].rule_value: {c.rule_kind} needs a whole number")
            if c.rule_kind in ("must_include", "must_not_include") and not (c.rule_value or "").strip():
                errors.append(f"checks[{i}].rule_value: {c.rule_kind} needs the phrase")
        return errors
