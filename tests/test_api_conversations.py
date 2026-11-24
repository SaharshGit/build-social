"""Conversation endpoints and message storage (the chat assistant itself comes next). No API calls."""

import uuid

from buildsocial.db import brands as brand_ops
from buildsocial.db import conversations as conv_ops
from buildsocial.db.models import MessageRole
from tests.test_api_runs import create_brand


async def test_create_list_and_open(client):
    brand_id = await create_brand(client)
    r = await client.post("/conversations", json={"brand_id": brand_id})
    assert r.status_code == 201
    conv = r.json()
    assert conv["title"] == "New chat" and conv["brand_id"] == brand_id and conv["messages"] == []

    assert [c["id"] for c in (await client.get("/conversations")).json()] == [conv["id"]]
    assert (await client.get(f"/conversations/{conv['id']}")).json()["id"] == conv["id"]


async def test_brand_is_optional_but_must_exist(client):
    assert (await client.post("/conversations", json={})).json()["brand_id"] is None
    assert (await client.post("/conversations", json={"brand_id": str(uuid.uuid4())})).status_code == 404


async def test_rename_and_switch_brand(client):
    brand_id = await create_brand(client)
    conv_id = (await client.post("/conversations", json={})).json()["id"]
    r = await client.patch(f"/conversations/{conv_id}", json={"title": "Monsoon ideas", "brand_id": brand_id})
    assert r.json()["title"] == "Monsoon ideas" and r.json()["brand_id"] == brand_id
    r = await client.patch(f"/conversations/{conv_id}", json={"title": "Renamed only"})
    assert r.json()["brand_id"] == brand_id  # fields not sent are left alone
    assert (await client.patch(f"/conversations/{uuid.uuid4()}", json={"title": "x"})).status_code == 404


async def test_messages_keep_their_order_and_update_recency(client, db_maker):
    first = (await client.post("/conversations", json={})).json()["id"]
    second = (await client.post("/conversations", json={})).json()["id"]
    async with db_maker() as db:
        org = await brand_ops.default_org(db)
        conv = await conv_ops.get_conversation(db, org, uuid.UUID(first))
        # Saved in ONE transaction: must still come back in the order they were added.
        await conv_ops.add_message(db, conv, MessageRole.USER, "How many posts are pending?")
        await conv_ops.add_message(db, conv, MessageRole.ASSISTANT, "2 posts.", [{"type": "posts", "ids": []}])
        await db.commit()

    opened = (await client.get(f"/conversations/{first}")).json()
    assert [m["role"] for m in opened["messages"]] == ["user", "assistant"]
    assert opened["messages"][1]["attachments"] == [{"type": "posts", "ids": []}]
    # The conversation with the newest message is listed first.
    assert [c["id"] for c in (await client.get("/conversations")).json()] == [first, second]
