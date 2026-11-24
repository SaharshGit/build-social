"""Conversation operations: chat threads and their messages, scoped to the organization."""

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from buildsocial.db.models import Brand, Conversation, Message, MessageRole, Organization


async def create_conversation(
    db: AsyncSession, org: Organization, brand_id: uuid.UUID | None = None, title: str = "New chat"
) -> Conversation:
    conv = Conversation(organization_id=org.id, brand_id=brand_id, title=title)
    db.add(conv)
    await db.flush()
    await db.refresh(conv)
    return conv


async def list_conversations(db: AsyncSession, org: Organization, limit: int = 50) -> list[Conversation]:
    result = await db.execute(
        select(Conversation).where(Conversation.organization_id == org.id)
        .order_by(Conversation.updated_at.desc()).limit(limit)
    )
    return list(result.scalars())


async def get_conversation(
    db: AsyncSession, org: Organization, conversation_id: uuid.UUID, with_messages: bool = False
) -> Conversation | None:
    q = select(Conversation).where(Conversation.id == conversation_id, Conversation.organization_id == org.id)
    if with_messages:
        q = q.options(selectinload(Conversation.messages))  # load messages in one extra query
    result = await db.execute(q)
    return result.scalar_one_or_none()


async def brand_in_org(db: AsyncSession, org: Organization, brand_id: uuid.UUID) -> bool:
    result = await db.execute(select(Brand.id).where(Brand.id == brand_id, Brand.organization_id == org.id))
    return result.scalar_one_or_none() is not None


async def add_message(
    db: AsyncSession, conv: Conversation, role: MessageRole, content: str, attachments: list[Any] | None = None
) -> Message:
    now = datetime.now(timezone.utc)
    msg = Message(conversation_id=conv.id, role=role, content=content, attachments=attachments or [], created_at=now)
    db.add(msg)
    conv.updated_at = now  # recent chats float to the top of the list
    await db.flush()
    await db.refresh(msg)
    return msg


async def recent_messages(db: AsyncSession, conv: Conversation, limit: int = 20) -> list[Message]:
    """The last `limit` messages, oldest first (what the assistant sees as context)."""
    result = await db.execute(
        select(Message).where(Message.conversation_id == conv.id).order_by(Message.created_at.desc()).limit(limit)
    )
    return list(reversed(list(result.scalars())))


async def delete_conversation(db: AsyncSession, conv: Conversation) -> None:
    """Delete a chat and its messages (posts and runs it started are kept)."""
    await db.delete(conv)  # messages go too (cascade on the relationship)
    await db.flush()
