"""Chat: one user message in -> the chat agent decides -> our code acts -> one assistant reply out.

The agent (agents/chat.py) only DECIDES. Everything that changes data happens here, through
the same functions the API's buttons use (create_run, decide, revision runs), with the same
rules. A run started from the chat is processed by the normal worker; the reply carries a
"run" card that the chat page polls, exactly like the run page.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.agents.chat import (
    ChatAgent, ChatBrand, ChatDecision, ChatInput, ChatListedPost, ChatPhoto, ChatPost, DataAnswerAgent,
    DataAnswerInput,
    resolve_brand,
)
from buildsocial.chat_data import run_query
from buildsocial.db import brands as brand_ops
from buildsocial.db import conversations as conv_ops
from buildsocial.db import posts as post_ops
from buildsocial.db import runs as run_ops
from buildsocial.db.models import (
    Asset, Brand, Conversation, Message, MessageRole, Organization, Post, PostStatus, Run, RunKind,
    RunStatus, RunTrigger,
)
from buildsocial.harness.agent import AgentError
from buildsocial.harness.llm.base import LLMClient, LLMError
from buildsocial.harness.llm.registry import Pricing, get_client
from buildsocial.harness.trace import Tracer
from buildsocial.pipeline import default_pricing
from buildsocial.schemas import PostBrief, PostDraft

HISTORY_MESSAGES = 12


@dataclass
class ChatDeps:
    """What the chat talks to; tests swap in fakes."""

    client_for: Callable[[str], LLMClient] = get_client
    pricing_for: Callable[[str], Pricing] = default_pricing
    tracer_for: Callable[[uuid.UUID], Tracer] = field(default=lambda conversation_id: Tracer())


async def handle_message(
    db: AsyncSession, org: Organization, conv: Conversation, text: str, deps: ChatDeps,
    photos: list[Asset] | None = None,
) -> tuple[Message, Message]:
    """Save the user's message, decide and act, save the assistant's reply. Returns (user, assistant).

    photos: images attached to this message (already checked to belong to the organization). A post
    created from this message is built around them.
    """
    photos = photos or []
    history = await conv_ops.recent_messages(db, conv, HISTORY_MESSAGES)
    user_msg = await conv_ops.add_message(db, conv, MessageRole.USER, text,
                                          [{"type": "photo", "id": str(a.id)} for a in photos])
    if conv.brand_id is None and photos:  # a photo says which brand the chat is about
        conv.brand_id = photos[0].brand_id
    if conv.title == "New chat":
        conv.title = short_title(text)

    brands = await brand_ops.list_brands(db, org)
    chat_brands = [ChatBrand(name=b.name, industry=b.industry, market=b.market) for b in brands]
    chat_brand = next((b.name for b in brands if b.id == conv.brand_id), None)
    post, run_active = await _current_post(db, history)
    listed = await _listed_posts(db, history, brands)

    chat_input = ChatInput(
        brands=chat_brands,
        chat_brand=chat_brand,
        post=await _describe(db, post) if post else None,
        run_in_progress=run_active,
        listed_posts=[ChatListedPost(brand=next(b.name for b in brands if b.id == p.brand_id), platform=p.platform,
                                     status=p.status, topic=p.topic,
                                     revision_in_progress=await post_ops.active_revision(db, p.id) is not None)
                      for p in listed],
        history=[_history_line(m) for m in history],
        message=text,
        photos=[ChatPhoto(brand=next((b.name for b in brands if b.id == a.brand_id), "?"),
                          description=a.description, product=a.product) for a in photos],
    )

    tracer = deps.tracer_for(conv.id)
    try:
        pricing = deps.pricing_for(ChatAgent.name)
        scoped = tracer.scoped(pricing.cost, pricing.currency)
        async with deps.client_for(ChatAgent.name) as llm:
            decision = (await ChatAgent().run(chat_input, llm, scoped)).output
            if decision.action == "lookup":
                reply, attachments = await _lookup(db, org, decision, chat_brand, text, llm, scoped)
            else:
                if decision.post_number:  # "the second one": a post from the last list
                    post = listed[decision.post_number - 1]
                reply, attachments = await _act(db, org, conv, brands, post, decision, photos)
    except (AgentError, LLMError) as e:
        reply, attachments = (
            "Sorry, I couldn't process that just now. Could you try again, or rephrase it?",
            [{"type": "error", "message": f"{type(e).__name__}: {str(e)[:300]}"}],
        )
    finally:
        tracer.close()

    assistant_msg = await conv_ops.add_message(db, conv, MessageRole.ASSISTANT, reply, attachments)
    return user_msg, assistant_msg


async def _act(
    db: AsyncSession, org: Organization, conv: Conversation, brands: list[Brand], post: Post | None,
    d: ChatDecision, photos: list[Asset] | None = None,
) -> tuple[str, list[dict[str, Any]]]:
    """Carry out the decision with the same rules as the API. Returns (reply, attachments)."""
    if d.action == "create_post":
        name = resolve_brand(d.brand, [ChatBrand(name=b.name, industry=b.industry, market=b.market) for b in brands])
        brand = next((b for b in brands if b.name == name), None) or next(
            (b for b in brands if b.id == conv.brand_id), None)
        if brand is None:  # check() should prevent this; stay safe anyway
            return "Which brand is this post for?", []
        conv.brand_id = brand.id  # naming another brand switches the chat to it
        used = [a for a in photos or [] if a.brand_id == brand.id]
        skipped = len(photos or []) - len(used)
        brief = PostBrief(topic=d.topic or "", platform=d.platform or "instagram", goal=d.goal or "engagement",
                          # a post built around a photo is an image post, whatever the model picked
                          format="single_image" if used else d.format or "text", notes=d.notes,
                          photo_ids=[str(a.id) for a in used])
        run = await run_ops.create_run(db, brand, brief, trigger=RunTrigger.CHAT, conversation_id=conv.id)
        note = f" (I left out {skipped} photo{'s' if skipped > 1 else ''} from another brand.)" if skipped else ""
        return d.reply + note, [{"type": "run", "id": str(run.id)}]

    if d.action in ("revise_post", "approve_post", "reject_post"):
        if post is None:
            return "There's no post in this chat yet. Want me to create one?", []
        if await post_ops.active_revision(db, post.id):
            return "A revision of this post is still in progress. Give it a few seconds.", [
                {"type": "post", "id": str(post.id)}]
        try:
            if d.action == "revise_post":
                if post.status != PostStatus.PENDING:
                    raise post_ops.InvalidTransition(f"post is already {post.status}")
                original = await db.get(Run, post.run_id)
                brand = await db.get(Brand, post.brand_id)
                run = await run_ops.create_run(db, brand, PostBrief.model_validate(original.brief),
                                               kind=RunKind.REVISION, feedback=d.feedback, revises_post_id=post.id,
                                               trigger=RunTrigger.CHAT, conversation_id=conv.id)
                return d.reply, [{"type": "run", "id": str(run.id)}]
            status = PostStatus.APPROVED if d.action == "approve_post" else PostStatus.REJECTED
            await post_ops.decide(db, post, status, d.reason)
            return d.reply, [{"type": "post", "id": str(post.id)}]
        except post_ops.InvalidTransition as e:
            return f"I can't do that: {e}.", [{"type": "post", "id": str(post.id)}]

    if d.post_number and post is not None:  # answer about a listed post: show it (it becomes the chat's post)
        return d.reply, [{"type": "post", "id": str(post.id)}]
    return d.reply, []  # answer / clarify: just the reply


async def _lookup(
    db: AsyncSession, org: Organization, d: ChatDecision, chat_brand: str | None, question: str,
    llm: LLMClient, tracer: Tracer,
) -> tuple[str, list[dict[str, Any]]]:
    """Run the data tool, then write the reply from its result only."""
    query = d.query()
    if query.tool == "brand_info" and not query.brand:
        query = query.model_copy(update={"brand": chat_brand})
    result = await run_query(db, org, query)
    card = {"type": "data", "query": query.model_dump(exclude_none=True), "result": result}
    try:
        answer = await DataAnswerAgent().run(DataAnswerInput(question=question, result=result), llm, tracer)
        return answer.output.reply, [card]
    except AgentError:
        # Couldn't write a reply that sticks to the data: show the data itself instead.
        return "Here's what I found:", [card]


async def _current_post(db: AsyncSession, history: list[Message]) -> tuple[Post | None, bool]:
    """The post this chat is about: from the newest message with a post or run card.

    Returns (post, a_new_post_is_still_being_created).
    """
    for msg in reversed(history):
        for a in reversed(msg.attachments or []):
            if a.get("type") == "post":
                return await db.get(Post, uuid.UUID(a["id"])), False
            if a.get("type") == "run":
                run = await db.get(Run, uuid.UUID(a["id"]))
                if run is None:
                    continue
                if run.kind == RunKind.REVISION and run.revises_post_id:
                    return await db.get(Post, run.revises_post_id), False
                post_id = await run_ops.post_id_for(db, run)
                if post_id:
                    return await db.get(Post, post_id), False
                return None, run.status in (RunStatus.QUEUED, RunStatus.RUNNING)
    return None, False


async def _listed_posts(db: AsyncSession, history: list[Message], brands: list[Brand]) -> list[Post]:
    """The posts in the newest list shown in this chat (a list_posts lookup), in the same order."""
    org_brand_ids = {b.id for b in brands}
    for msg in reversed(history):
        for a in reversed(msg.attachments or []):
            if a.get("type") == "data" and "posts" in (a.get("result") or {}):
                posts = [await db.get(Post, uuid.UUID(p["id"])) for p in a["result"]["posts"]]
                return [p for p in posts if p is not None and p.brand_id in org_brand_ids]
    return []


async def _describe(db: AsyncSession, post: Post) -> ChatPost:
    """What the chat agent is told about the current post."""
    facts = []
    for f in post.facts:
        sites = ", ".join(s.get("title") or "source" for s in f.get("sources", []))
        facts.append(f"[{f.get('scope', 'fact')}] {f['fact']} ({sites or 'no source'})")
    issues = [f"{i['type']}: \"{i['quote']}\": {i['problem']}" for i in post.review.get("issues", [])]
    return ChatPost(
        status=post.status,
        revision_in_progress=await post_ops.active_revision(db, post.id) is not None,
        platform=post.platform,
        topic=post.topic,
        full_text=PostDraft.model_validate(post.draft).full_text,
        angle=post.plan.get("angle", ""),
        key_message=post.plan.get("key_message", ""),
        facts=facts,
        open_issues=issues,
    )


def short_title(text: str, limit: int = 50) -> str:
    """The first message, cut at a word boundary: "Create an Instagram post about…"."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text or "New chat"
    return text[:limit].rsplit(" ", 1)[0].rstrip(",.;:") + "…"


def _history_line(m: Message) -> str:
    cards = {"run": "[started a run]", "post": "[showed the post]", "data": "[showed data]", "error": "[error]",
             "photo": "[attached a photo]"}
    extra = " ".join(cards.get(a.get("type"), "") for a in m.attachments or [])
    return f"{m.role}: {m.content}{(' ' + extra) if extra.strip() else ''}"
