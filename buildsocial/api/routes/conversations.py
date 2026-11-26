"""Conversation endpoints: chat threads with the BuildSocial assistant."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.api.deps import current_org, get_chat_deps, get_db
from buildsocial.api.schemas import ChatTurnOut, ConversationIn, ConversationOut, ConversationSummary, MessageIn, MessageOut
from buildsocial.chat import ChatDeps, handle_message
from buildsocial.db import assets as asset_ops
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


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: uuid.UUID, db: AsyncSession = Depends(get_db),
                              org: Organization = Depends(current_org)):
    """Delete a chat. Posts and runs started from it are kept."""
    conv = await conv_ops.get_conversation(db, org, conversation_id, with_messages=True)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    await conv_ops.delete_conversation(db, conv)


@router.post("/{conversation_id}/messages", response_model=ChatTurnOut)
async def send_message(conversation_id: uuid.UUID, body: MessageIn, db: AsyncSession = Depends(get_db),
                       org: Organization = Depends(current_org), deps: ChatDeps = Depends(get_chat_deps)):
    """Send a message; the assistant decides what to do, does it, and replies.

    Waits for the assistant (a few seconds). If it started a post or a revision, the reply has a
    {"type": "run"} attachment: poll GET /runs/{id} for progress, like the New post page.
    """
    conv = await conv_ops.get_conversation(db, org, conversation_id)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "conversation not found")
    if not body.text.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "message is empty")
    photos = []
    for photo_id in dict.fromkeys(body.photo_ids):  # keep order, drop repeats
        asset = await asset_ops.get_asset(db, org, photo_id)
        if asset is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"photo {photo_id} not found")
        photos.append(asset)
    user_msg, assistant_msg = await handle_message(db, org, conv, body.text.strip(), deps, photos)
    return ChatTurnOut(user=MessageOut.from_row(user_msg), assistant=MessageOut.from_row(assistant_msg))
