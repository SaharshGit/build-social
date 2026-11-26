"""Chat agent: reads a chat message and DECIDES what to do; our code carries it out.

The model never acts by itself. It fills in a ChatDecision choosing one of a fixed set of
actions; check() rejects decisions that can't be carried out (unknown brand, no pending post
to approve, missing topic...) so the model has to fix them before anything happens. The
code that then acts (buildsocial/chat.py) uses the same functions as the API's buttons.
"""

import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from buildsocial.chat_data import TOOLS_HELP, DataQuery, Tool
from buildsocial.harness.agent import Agent
from buildsocial.schemas import Format, Goal, Platform

PROMPTS_DIR = Path(__file__).parent / "prompts"

Action = Literal["create_post", "revise_post", "approve_post", "reject_post", "lookup", "answer", "clarify"]


class ChatDecision(BaseModel):
    action: Action = Field(description="What to do. Exactly one action per message.")
    # lookup (flat fields: cheaper models fill these more reliably than a nested object)
    tool: Tool | None = Field(default=None, description="lookup: which data tool to run")
    status: Literal["pending", "approved", "rejected"] | None = Field(
        default=None, description="lookup: only posts with this status")
    days: int | None = Field(default=None, ge=1, le=365, description="lookup: only the last N days; empty = all time")
    # create_post (brand and platform are also lookup filters)
    brand: str | None = Field(default=None, description="create_post / lookup: the brand's exact name from the list")
    topic: str | None = Field(default=None, description="create_post: what the post is about")
    platform: Platform | None = Field(default=None, description="create_post: instagram or linkedin; lookup: filter")
    goal: Goal | None = Field(default=None, description="create_post: engagement / awareness / sales / community")
    format: Format | None = Field(default=None, description="create_post: 'single_image' if the user asks for an "
                                  "image, graphic, poster or creative; otherwise 'text'")
    notes: str | None = Field(default=None, description="create_post: anything the post must mention or avoid")
    # revise_post / reject_post
    feedback: str | None = Field(default=None, description="revise_post: what should change, in the user's words")
    reason: str | None = Field(default=None, description="reject_post: the user's reason, if they gave one")
    # revise / approve / reject / answer / clarify
    post_number: int | None = Field(
        default=None, ge=1,
        description="If the user means a post from the last list (e.g. 'the second one'), its number there; "
                    "empty = the post this chat is about")
    # always
    reply: str = Field(description="What to say to the user, short and friendly")

    def query(self) -> DataQuery:
        """The lookup's data query, from the flat fields."""
        return DataQuery(tool=self.tool, brand=self.brand, status=self.status, platform=self.platform, days=self.days)


class ChatBrand(BaseModel):
    name: str
    industry: str
    market: str


class ChatPost(BaseModel):
    """The post this chat is currently about (its latest one), as the agent sees it."""

    status: str  # pending / approved / rejected
    revision_in_progress: bool = False
    platform: str
    topic: str
    full_text: str
    angle: str
    key_message: str
    facts: list[str]  # "[scope] fact (source titles)"
    open_issues: list[str]  # "type: quote: problem"


class ChatListedPost(BaseModel):
    """A post from the last list shown in the chat (a lookup's list_posts result)."""

    brand: str
    platform: str
    status: str
    topic: str
    revision_in_progress: bool = False


class ChatInput(BaseModel):
    brands: list[ChatBrand]
    chat_brand: str | None  # the brand chosen for this chat
    post: ChatPost | None
    run_in_progress: bool = False  # a post is being created right now
    listed_posts: list[ChatListedPost] = []  # the last list of posts shown, numbered from 1
    history: list[str]  # recent messages, oldest first: "user: ..." / "assistant: ..."
    message: str


class ChatAgent(Agent[ChatInput, ChatDecision]):
    name = "chat"
    output_type = ChatDecision
    system_prompt = (PROMPTS_DIR / "chat.md").read_text().replace("{tools}", TOOLS_HELP)
    max_output_tokens = 800

    def build_user_message(self, input: ChatInput) -> str:
        brands = "\n".join(f"- {b.name} ({b.industry}, {b.market})" for b in input.brands) or "- (none)"
        lines = [
            "# Brands",
            brands,
            "",
            f"Brand chosen for this chat: {input.chat_brand or '(none)'}",
            "",
        ]
        if input.post:
            p = input.post
            lines += [
                "# The post this chat is about",
                f"Status: {p.status}{' (a revision is in progress)' if p.revision_in_progress else ''}",
                f"Platform: {p.platform} | Topic: {p.topic}",
                f"Angle: {p.angle}",
                f"Key message: {p.key_message}",
                "Text:",
                p.full_text,
                "Facts it used:",
                *([f"- {f}" for f in p.facts] or ["- (none)"]),
                "Open review issues:",
                *([f"- {i}" for i in p.open_issues] or ["- (none)"]),
                "",
            ]
        else:
            lines += ["# The post this chat is about", "(none yet)", ""]
        if input.run_in_progress:
            lines += ["A post is being created right now (not finished yet).", ""]
        if input.listed_posts:
            lines += ["# Posts in the last list shown (refer to them with post_number)",
                      *[f"{i}. {p.brand} | {p.platform} | {p.status}"
                        f"{' (revision in progress)' if p.revision_in_progress else ''} | {p.topic}"
                        for i, p in enumerate(input.listed_posts, 1)], ""]
        lines += ["# Conversation so far", *(input.history or ["(this is the first message)"]), "",
                  "# The user's new message", input.message]
        return "\n".join(lines)

    def check(self, output: ChatDecision, input: ChatInput) -> list[str]:
        errors: list[str] = []
        a = output.action
        if not output.reply.strip():
            errors.append("reply: must not be empty")

        if a == "create_post":
            brand = output.brand or input.chat_brand
            names = [b.name for b in input.brands]
            if not brand:
                errors.append("create_post: no brand given and none chosen for this chat; use clarify to ask which")
            elif brand.lower() not in {n.lower() for n in names}:
                errors.append(f"brand: '{brand}' isn't one of the brands {names}; use an exact name or clarify")
            if not (output.topic or "").strip():
                errors.append("create_post: topic is missing; use clarify to ask what the post is about")
            if output.platform is None:
                errors.append("create_post: platform is missing; use clarify to ask (instagram or linkedin)")
            if input.run_in_progress:
                errors.append("create_post: a post is already being created in this chat; tell the user to wait")

        n = output.post_number
        if n is not None and n > len(input.listed_posts):
            errors.append(f"post_number: {n} isn't in the last list ({len(input.listed_posts)} posts); "
                          "leave it empty or clarify")
        elif a in ("revise_post", "approve_post", "reject_post"):
            target = input.listed_posts[n - 1] if n else input.post
            if target is None:
                errors.append(f"{a}: this chat has no post yet; use clarify or create_post instead")
            elif target.status != "pending":
                errors.append(f"{a}: the post is already {target.status}; use answer to tell the user")
            elif target.revision_in_progress:
                errors.append(f"{a}: a revision is in progress; use answer to tell the user to wait")
        if a == "lookup":
            names = {b.name.lower() for b in input.brands}
            if output.tool is None:
                errors.append("lookup: set `tool` (count_posts, list_posts, run_stats, spend or brand_info)")
            elif output.brand and output.brand.lower() not in names:
                errors.append(f"brand: '{output.brand}' isn't one of the brands; use an exact name or leave it empty")
            elif output.tool == "brand_info" and not (output.brand or input.chat_brand):
                errors.append("lookup brand_info: say which brand, or use clarify to ask")
        if a == "revise_post" and not (output.feedback or "").strip():
            errors.append("revise_post: feedback is missing; use clarify to ask what should change")
        return errors


def resolve_brand(name: str | None, brands: list[ChatBrand]) -> str | None:
    """Exact brand name from the list, matching case-insensitively."""
    if not name:
        return None
    return next((b.name for b in brands if b.name.lower() == name.lower()), None)


# --- Answering from data ---------------------------------------------------------------

class DataAnswer(BaseModel):
    reply: str = Field(description="The answer, written only from the data")


class DataAnswerInput(BaseModel):
    question: str
    result: dict[str, Any]


NUMBER = re.compile(r"\d+(?:[.,]\d+)*")


def numbers_in(text: str) -> set[str]:
    """Numbers as written, normalized: "1,200" -> "1200", "0.50" -> "0.5"."""
    out = set()
    for n in NUMBER.findall(text):
        n = n.replace(",", "")
        out.add(n.rstrip("0").rstrip(".") if "." in n else n)
    return out


class DataAnswerAgent(Agent[DataAnswerInput, DataAnswer]):
    """Turns a data tool's result into a short reply. check() rejects any number that isn't
    in the result (or the question), so it can't make up counts or costs."""

    name = "chat"
    output_type = DataAnswer
    system_prompt = (PROMPTS_DIR / "chat_answer.md").read_text()
    max_output_tokens = 500

    def build_user_message(self, input: DataAnswerInput) -> str:
        return f"# Question\n{input.question}\n\n# Data (the only source you may use)\n" + json.dumps(
            input.result, indent=1, ensure_ascii=False)

    def check(self, output: DataAnswer, input: DataAnswerInput) -> list[str]:
        if not output.reply.strip():
            return ["reply: must not be empty"]
        allowed = numbers_in(json.dumps(input.result)) | numbers_in(input.question)
        invented = sorted(numbers_in(output.reply) - allowed)
        if invented:
            return [f"reply: these numbers aren't in the data: {invented}. Use only numbers from the data, "
                    "exactly as given; don't calculate new ones."]
        return []
