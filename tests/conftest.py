"""Shared test fixtures.

API tests use a throwaway in-memory SQLite database (same tables, created from the
models), so they need no Docker and never touch the real Postgres data.
"""

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from buildsocial.api.app import create_app
from buildsocial.api.deps import get_chat_deps, get_db
from buildsocial.chat import ChatDeps
from buildsocial.db.models import Base


@pytest.fixture
async def db_maker():
    # StaticPool + one connection: every session sees the same in-memory database.
    engine = create_async_engine(
        "sqlite+aiosqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def client(db_maker):
    """An HTTP client talking to the real app, with the database swapped for the test one."""

    async def test_db():
        async with db_maker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    app = create_app()
    app.dependency_overrides[get_db] = test_db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest.fixture
def chat(client):
    """chat(*replies): script the chat assistant's next LLM replies (decisions, then answers)."""
    from tests.test_api_runs import INR, scripted

    def script(*replies):
        client_for, queues = scripted(chat=list(replies))
        client._transport.app.dependency_overrides[get_chat_deps] = lambda: ChatDeps(
            client_for=client_for, pricing_for=lambda n: INR)
        return queues["chat"]

    return script
