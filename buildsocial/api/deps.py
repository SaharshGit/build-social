"""Things endpoints receive automatically ("dependencies" in FastAPI terms)."""

from collections.abc import AsyncIterator

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.db import brands as brand_ops
from buildsocial.db.models import Organization
from buildsocial.db.session import session


async def get_db() -> AsyncIterator[AsyncSession]:
    """One database session per request: committed if the request succeeds, rolled back if not.

    Tests replace this with a session on a throwaway database.
    """
    async with session() as s:
        yield s


async def current_org(db: AsyncSession = Depends(get_db)) -> Organization:
    """The organization making the request. No login yet, so it's always the default one."""
    return await brand_ops.default_org(db)
