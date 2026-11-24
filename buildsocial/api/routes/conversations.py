"""Conversation endpoints: chat threads with the BuildSocial assistant.
(Sending a message and getting the assistant's reply is added in the next step.)"""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.api.deps import current_org, get_db
from buildsocial.api.schemas import ConversationIn, ConversationOut, ConversationSummary
from buildsocial.db import conversations as conv_ops
from buildsocial.db.models import Organization

router = APIRouter(prefix="/conversations", tags=["chat"])


async def _check_brand(db: AsyncSession, org: Organization, brand_id: uuid.UUID | None) -> None:
    if brand_id is not None and not await conv_ops.brand_in_org(db, org, brand_id):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "brand not found")


@router.post("", response_model=ConversationOut, status_code=status.HTTP_201_CREATED)
async def create_conversation(body: ConversationIn, db: AsyncSession = Depends(get_db),
                              org: Organization = Depends(current_org)):
    await _check_brand(db, org, body.brand_id)
    conv = await conv_ops.create_conversation(db, org, body.brand_id, body.title or "New chat")
    conv = await conv_ops.get_conversation(db, org, conv.id, with_messages=True)
    return ConversationOut.from_row(conv)


@router.get("", response_model=list[ConversationSummary])
async def list_conversations(limit: int = 50, db: AsyncSession = Depends(get_db),
                             org: Organization = Depends(current_org)):
    """Most recently active first (for the chat sidebar)."""
    return [ConversationSummary.from_row(c) for c in await conv_ops.list_conversations(db, org, min(limit, 200))]


@router.get("/{conversation_id}", response_model=ConversationOut)
async def get_conversation(conversation_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                           org: Organization = Depends(current_org)):
    conv = await conv_ops.get_conversation(db, org, conversation_id, with_messages=True)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    return ConversationOut.from_row(conv)


@router.patch("/{conversation_id}", response_model=ConversationOut)
async def update_conversation(conversation_id: uuid.UUID, body: ConversationIn,
                              db: AsyncSession = Depends(get_db), org: Organization = Depends(current_org)):
    """Rename the chat and/or change its brand. Only the fields you send change."""
    conv = await conv_ops.get_conversation(db, org, conversation_id)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    if "brand_id" in body.model_fields_set:
        await _check_brand(db, org, body.brand_id)
        conv.brand_id = body.brand_id
    if body.title:
        conv.title = body.title
    await db.flush()
    conv = await conv_ops.get_conversation(db, org, conversation_id, with_messages=True)
    return ConversationOut.from_row(conv)
