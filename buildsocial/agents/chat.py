"""Chat agent: reads a chat message and DECIDES what to do; our code carries it out.

The model never acts by itself. It fills in a ChatDecision choosing one of a fixed set of
actions; check() rejects decisions that can't be carried out (unknown brand, no pending post
to approve, missing topic...) so the model has to fix them before anything happens. The
code that then acts (buildsocial/chat.py) uses the same functions as the API's buttons.
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from buildsocial.harness.agent import Agent
from buildsocial.schemas import Goal, Platform

PROMPTS_DIR = Path(__file__).parent / "prompts"

Action = Literal["create_post", "revise_post", "approve_post", "reject_post", "answer", "clarify"]


class ChatDecision(BaseModel):
    action: Action = Field(description="What to do. Exactly one action per message.")
    # create_post
    brand: str | None = Field(default=None, description="create_post: the brand's exact name from the list")
    topic: str | None = Field(default=None, description="create_post: what the post is about")
    platform: Platform | None = Field(default=None, description="create_post: instagram or linkedin")
    goal: Goal | None = Field(default=None, description="create_post: engagement / awareness / sales / community")
    notes: str | None = Field(default=None, description="create_post: anything the post must mention or avoid")
    # revise_post / reject_post
    feedback: str | None = Field(default=None, description="revise_post: what should change, in the user's words")
    reason: str | None = Field(default=None, description="reject_post: the user's reason, if they gave one")
    # always
    reply: str = Field(description="What to say to the user, short and friendly")


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


class ChatInput(BaseModel):
    brands: list[ChatBrand]
    chat_brand: str | None  # the brand chosen for this chat
    post: ChatPost | None
    run_in_progress: bool = False  # a post is being created right now
    history: list[str]  # recent messages, oldest first: "user: ..." / "assistant: ..."
    message: str


class ChatAgent(Agent[ChatInput, ChatDecision]):
    name = "chat"
    output_type = ChatDecision
    system_prompt = (PROMPTS_DIR / "chat.md").read_text()
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

        if a in ("revise_post", "approve_post", "reject_post"):
            if input.post is None:
                errors.append(f"{a}: this chat has no post yet; use clarify or create_post instead")
            elif input.post.status != "pending":
                errors.append(f"{a}: the post is already {input.post.status}; use answer to tell the user")
            elif input.post.revision_in_progress:
                errors.append(f"{a}: a revision is in progress; use answer to tell the user to wait")
        if a == "revise_post" and not (output.feedback or "").strip():
            errors.append("revise_post: feedback is missing; use clarify to ask what should change")
        return errors


def resolve_brand(name: str | None, brands: list[ChatBrand]) -> str | None:
    """Exact brand name from the list, matching case-insensitively."""
    if not name:
        return None
    return next((b.name for b in brands if b.name.lower() == name.lower()), None)
