"""Things endpoints receive automatically ("dependencies" in FastAPI terms)."""

from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.chat import ChatDeps
from buildsocial.db import brands as brand_ops
from buildsocial.db.models import Organization
from buildsocial.db.session import session
from buildsocial.harness.storage import Storage, get_storage
from buildsocial.harness.trace import tracer_for_storage
from buildsocial.photo_reading import ModelDeps


async def get_db() -> AsyncIterator[AsyncSession]:
    """One database session per request: committed if the request succeeds, rolled back if not.

    Tests replace this with a session on a throwaway database.
    """
    async with session() as s:
        yield s


async def current_org(db: AsyncSession = Depends(get_db)) -> Organization:
    """The organization making the request. No login yet, so it's always the default one."""
    return await brand_ops.default_org(db)


def get_store() -> Storage:
    """Where files (uploaded images) go: the configured bucket or folder. Tests use memory storage."""
    return get_storage()


def get_model_deps() -> ModelDeps:
    """Models for one-off model steps (e.g. reading an uploaded photo), traced like the chat's."""
    store = get_storage()
    return ModelDeps(tracer_for=lambda label: tracer_for_storage(store, label))


def get_chat_deps() -> ChatDeps:
    """The chat's LLM clients and tracing (traces go to the same local folder / bucket as the CLI's).

    Tests replace this with fake LLMs.
    """
    store = get_storage()
    return ChatDeps(tracer_for=lambda conversation_id: tracer_for_storage(store, f"chat_{str(conversation_id)[:8]}"))
