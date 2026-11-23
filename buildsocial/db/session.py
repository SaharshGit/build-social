"""Database connection: one engine per process, short-lived sessions per unit of work.

DATABASE_URL (in .env) picks the database: local Docker Postgres now, Cloud SQL later.
Only that setting changes between them.
"""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from functools import lru_cache

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine


def database_url() -> str:
    from dotenv import load_dotenv

    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set in .env (see .env.example)")
    return url


@lru_cache(maxsize=1)
def engine() -> AsyncEngine:
    # pool_pre_ping: check a pooled connection still works before using it (survives DB restarts).
    return create_async_engine(database_url(), pool_pre_ping=True)


@asynccontextmanager
async def session() -> AsyncIterator[AsyncSession]:
    """`async with session() as s:` ... commits on success, rolls back on error."""
    maker = async_sessionmaker(engine(), expire_on_commit=False)
    async with maker() as s:
        try:
            yield s
            await s.commit()
        except Exception:
            await s.rollback()
            raise
