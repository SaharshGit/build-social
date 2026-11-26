"""Chat tests: the assistant's decisions are scripted (fake LLM), so these check what OUR code
does with each decision: start runs, approve/reject/revise with the normal rules, refuse what
isn't allowed. No API calls, no cost."""

import json
import uuid

import yaml

from buildsocial.chat import short_title
from buildsocial.db.models import Run, RunKind, RunStatus
from buildsocial.harness.llm.base import LLMError
from buildsocial.worker import process_one
from tests.test_api_runs import EXAMPLES, create_brand, happy_path, worker_deps


def decision(action, reply="ok", **fields):
    return json.dumps({"action": action, "reply": reply, **fields})


async def new_chat(client, brand_id=None):
    return (await client.post("/conversations", json={"brand_id": brand_id} if brand_id else {})).json()["id"]


async def say(client, conv_id, text):
    r = await client.post(f"/conversations/{conv_id}/messages", json={"text": text})
    assert r.status_code == 200, r.text
    return r.json()


async def make_post(client, chat, db_maker, conv_id):
    """Ask for a post in the chat and let the worker finish it. Returns the post id."""
    chat(decision("create_post", "On it!", topic="monsoon", platform="instagram"))
    run_id = (await say(client, conv_id, "monsoon post for instagram"))["assistant"]["attachments"][0]["id"]
    client_for, _ = happy_path()
    await process_one(worker_deps(db_maker, client_for))
    return (await client.get(f"/runs/{run_id}")).json()["post_id"]


async def test_create_post_queues_a_run_and_titles_the_chat(client, chat, db_maker):
    brand_id = await create_brand(client)
    conv = await new_chat(client)
    chat(decision("create_post", "Starting a monsoon post for Brew House.", brand="brew house",
                  topic="monsoon evenings", platform="instagram"))

    turn = await say(client, conv, "Make an instagram post about monsoon evenings for Brew House")
    assert turn["user"]["content"].startswith("Make an instagram post")
    assert turn["assistant"]["content"] == "Starting a monsoon post for Brew House."
    card = turn["assistant"]["attachments"][0]
    assert card["type"] == "run"

    run = (await client.get(f"/runs/{card['id']}")).json()
    assert run["status"] == "queued" and run["brief"]["topic"] == "monsoon evenings"
    assert run["brief"]["goal"] == "engagement"  # default when the user didn't say
    assert run["brief"]["format"] == "text"

    c = (await client.get(f"/conversations/{conv}")).json()
    assert c["brand_id"] == brand_id  # naming the brand sets it for the chat
    assert c["title"] == "Make an instagram post about monsoon evenings for…"
    assert [m["role"] for m in c["messages"]] == ["user", "assistant"]


async def test_create_image_post_from_chat(client, chat):
    await create_brand(client)
    conv = await new_chat(client)
    chat(decision("create_post", "On it", brand="Brew House", topic="cold brew", platform="instagram",
                  format="single_image"))
    turn = await say(client, conv, "Make an image post about cold brew")
    run = (await client.get(f"/runs/{turn['assistant']['attachments'][0]['id']}")).json()
    assert run["brief"]["format"] == "single_image"


async def test_bad_decision_is_sent_back_to_the_model(client, chat):
    await create_brand(client)
    conv = await new_chat(client)
    # 1st try: platform missing -> check() error -> the model fixes it by asking.
    q = chat(decision("create_post", "On it", brand="Brew House", topic="monsoon"),
             decision("clarify", "Instagram or LinkedIn?"))
    turn = await say(client, conv, "monsoon post for brew house")
    assert turn["assistant"]["content"] == "Instagram or LinkedIn?"
    assert turn["assistant"]["attachments"] == [] and q == []


async def test_unknown_brand_is_refused(client, chat):
    await create_brand(client)
    conv = await new_chat(client)
    chat(decision("create_post", "On it", brand="Starbucks", topic="x", platform="instagram"),
         decision("clarify", "I only know Brew House. Is it for them?"))
    turn = await say(client, conv, "starbucks post")
    assert "Brew House" in turn["assistant"]["content"] and turn["assistant"]["attachments"] == []


async def test_approve_the_chat_post(client, chat, db_maker):
    conv = await new_chat(client, await create_brand(client))
    post_id = await make_post(client, chat, db_maker, conv)

    chat(decision("approve_post", "Approved!"))
    turn = await say(client, conv, "looks good, approve it")
    assert turn["assistant"]["attachments"] == [{"type": "post", "id": post_id}]
    assert (await client.get(f"/posts/{post_id}")).json()["status"] == "approved"

    # Now it's approved: approving again isn't possible, the model is told and answers instead.
    chat(decision("approve_post", "Approved!"), decision("answer", "It's already approved."))
    turn = await say(client, conv, "approve it")
    assert turn["assistant"]["content"] == "It's already approved."


async def test_reject_keeps_the_reason(client, chat, db_maker):
    conv = await new_chat(client, await create_brand(client))
    post_id = await make_post(client, chat, db_maker, conv)
    chat(decision("reject_post", "Rejected.", reason="too generic"))
    await say(client, conv, "reject, too generic")
    post = (await client.get(f"/posts/{post_id}")).json()
    assert post["status"] == "rejected"
    assert any(n["text"] == "too generic" for n in post["feedback"])


async def test_revise_starts_a_revision_run(client, chat, db_maker):
    conv = await new_chat(client, await create_brand(client))
    post_id = await make_post(client, chat, db_maker, conv)
    chat(decision("revise_post", "Revising.", feedback="make the hook a question"))
    turn = await say(client, conv, "make the hook a question")
    run_id = turn["assistant"]["attachments"][0]["id"]
    async with db_maker() as db:
        run = await db.get(Run, uuid.UUID(run_id))
        assert run.kind == RunKind.REVISION and str(run.revises_post_id) == post_id
        assert run.feedback == "make the hook a question" and run.status == RunStatus.QUEUED

    # While it runs, the chat still knows which post it's about, and blocks another change.
    chat(decision("approve_post", "ok"), decision("answer", "Wait for the revision to finish."))
    turn = await say(client, conv, "approve")
    assert turn["assistant"]["content"] == "Wait for the revision to finish."


async def test_approve_with_no_post(client, chat):
    conv = await new_chat(client, await create_brand(client))
    chat(decision("approve_post", "Approved"), decision("clarify", "There's no post yet. Want one?"))
    turn = await say(client, conv, "approve")
    assert turn["assistant"]["content"] == "There's no post yet. Want one?"


async def test_no_second_post_while_one_is_being_made(client, chat):
    conv = await new_chat(client, await create_brand(client))
    chat(decision("create_post", "On it", topic="monsoon", platform="instagram"))
    await say(client, conv, "monsoon post, instagram")
    chat(decision("create_post", "On it", topic="diwali", platform="instagram"),
         decision("answer", "One post is still being made; give it a moment."))
    turn = await say(client, conv, "also a diwali one")
    assert turn["assistant"]["attachments"] == []


async def test_llm_failure_gives_a_polite_reply(client, chat):
    conv = await new_chat(client)
    chat(LLMError("provider down"))
    turn = await say(client, conv, "hi")
    assert "try again" in turn["assistant"]["content"]
    assert turn["assistant"]["attachments"][0]["type"] == "error"
    # Both messages are saved, so the chat shows what happened.
    assert len((await client.get(f"/conversations/{conv}")).json()["messages"]) == 2


async def test_empty_message_and_unknown_chat(client, chat):
    conv = await new_chat(client)
    assert (await client.post(f"/conversations/{conv}/messages", json={"text": "  "})).status_code == 422
    r = await client.post(f"/conversations/{uuid.uuid4()}/messages", json={"text": "hi"})
    assert r.status_code == 404


async def test_act_on_a_post_from_a_list(client, chat, db_maker):
    conv = await new_chat(client, await create_brand(client))
    post_id = await make_post(client, chat, db_maker, conv)

    # A list is shown; "approve the first one" points at it by number.
    chat(decision("lookup", "Let me check.", tool="list_posts", status="pending"),
         json.dumps({"reply": "One pending post: monsoon."}))
    await say(client, conv, "show pending posts")
    q = chat(decision("approve_post", "Approved.", post_number=3),  # not in the list -> sent back
             decision("approve_post", "Approved.", post_number=1))
    turn = await say(client, conv, "approve the first one")
    assert q == [] and turn["assistant"]["attachments"] == [{"type": "post", "id": post_id}]
    assert (await client.get(f"/posts/{post_id}")).json()["status"] == "approved"


async def test_show_a_listed_post(client, chat, db_maker):
    conv = await new_chat(client, await create_brand(client))
    post_id = await make_post(client, chat, db_maker, conv)
    chat(decision("lookup", "Let me check.", tool="list_posts"), json.dumps({"reply": "One post."}))
    await say(client, conv, "list posts")
    chat(decision("answer", "Here it is.", post_number=1))
    turn = await say(client, conv, "show me the first one")
    assert turn["assistant"]["attachments"] == [{"type": "post", "id": post_id}]


async def test_delete_chat_keeps_posts(client, chat, db_maker):
    conv = await new_chat(client, await create_brand(client))
    post_id = await make_post(client, chat, db_maker, conv)
    assert (await client.delete(f"/conversations/{conv}")).status_code == 204
    assert (await client.get(f"/conversations/{conv}")).status_code == 404
    assert (await client.get(f"/posts/{post_id}")).status_code == 200
    assert (await client.delete(f"/conversations/{conv}")).status_code == 404


def test_short_title():
    assert short_title("How many posts are pending?") == "How many posts are pending?"
    t = short_title("Create an Instagram post about monsoon evenings at our cafe in Bengaluru, please")
    assert t == "Create an Instagram post about monsoon evenings…"


async def test_runs_record_what_started_them(client, chat, db_maker):
    brand_id = await create_brand(client)
    # From the form (POST /brands/{id}/runs)
    form_run = (await client.post(f"/brands/{brand_id}/runs", json={"topic": "x", "platform": "instagram"})).json()
    assert form_run["trigger"] == "form" and form_run["conversation_id"] is None
    # From the chat
    conv = await new_chat(client, brand_id)
    chat(decision("create_post", "On it", topic="monsoon", platform="instagram"))
    run_id = (await say(client, conv, "monsoon post"))["assistant"]["attachments"][0]["id"]
    chat_run = (await client.get(f"/runs/{run_id}")).json()
    assert chat_run["trigger"] == "chat" and chat_run["conversation_id"] == conv
    # And the list shows both
    triggers = {r["id"]: r["trigger"] for r in (await client.get("/runs")).json()}
    assert triggers == {form_run["id"]: "form", run_id: "chat"}


# --- photos attached to a message ---------------------------------------------------------

from tests.test_api_assets import image_bytes, store, upload  # noqa: E402,F401 (store is a fixture)


async def say_with_photos(client, conv_id, text, photo_ids):
    r = await client.post(f"/conversations/{conv_id}/messages", json={"text": text, "photo_ids": photo_ids})
    assert r.status_code == 200, r.text
    return r.json()


async def test_a_post_is_built_around_an_attached_photo(client, chat, store):  # noqa: F811
    brand_id = await create_brand(client)
    photo = (await upload(client, brand_id, image_bytes(), in_library=False)).json()
    conv = await new_chat(client)  # no brand chosen yet: the photo's brand is used
    q = chat(decision("create_post", "On it", topic="cold brew", platform="instagram", format="text"))

    turn = await say_with_photos(client, conv, "Make a post around this photo", [photo["id"]])
    assert turn["user"]["attachments"] == [{"type": "photo", "id": photo["id"]}]
    run = (await client.get(f"/runs/{turn['assistant']['attachments'][0]['id']}")).json()
    assert run["brief"]["photo_ids"] == [photo["id"]]
    assert run["brief"]["format"] == "single_image"  # a photo means an image post, whatever the model said
    assert (await client.get(f"/conversations/{conv}")).json()["brand_id"] == brand_id
    assert q == []  # one model call: the decision


async def test_the_chat_agent_is_told_about_the_photo(client, chat, store):  # noqa: F811
    from buildsocial.agents.chat import ChatAgent, ChatBrand, ChatInput, ChatPhoto
    msg = ChatAgent().build_user_message(ChatInput(
        brands=[ChatBrand(name="Brew House", industry="cafe", market="Bengaluru")], chat_brand="Brew House",
        post=None, history=[], message="post this",
        photos=[ChatPhoto(brand="Brew House", description="A glass of iced tea.", product="Masala Chai Cold Brew")]))
    assert "# Photos attached to this message" in msg
    assert "- Brew House: A glass of iced tea. [product: Masala Chai Cold Brew]" in msg


async def test_photos_of_another_brand_are_left_out(client, chat, store):  # noqa: F811
    brand_id = await create_brand(client)
    profile = yaml.safe_load((EXAMPLES / "brew-house.yaml").read_text()) | {"name": "Other cafe"}
    other_id = (await client.post("/brands", json=profile)).json()["id"]
    mine = (await upload(client, brand_id, image_bytes(), in_library=False)).json()
    theirs = (await upload(client, other_id, image_bytes(), in_library=False)).json()
    conv = await new_chat(client, brand_id)
    chat(decision("create_post", "On it.", topic="cold brew", platform="instagram"))

    turn = await say_with_photos(client, conv, "Post these", [mine["id"], theirs["id"]])
    assert turn["assistant"]["content"] == "On it. (I left out 1 photo from another brand.)"
    run = (await client.get(f"/runs/{turn['assistant']['attachments'][0]['id']}")).json()
    assert run["brief"]["photo_ids"] == [mine["id"]]


async def test_unknown_photos_and_too_many_are_rejected(client, chat, store):  # noqa: F811
    conv = await new_chat(client)
    r = await client.post(f"/conversations/{conv}/messages", json={"text": "hi", "photo_ids": [str(uuid.uuid4())]})
    assert r.status_code == 404 and "photo" in r.json()["detail"]
    r = await client.post(f"/conversations/{conv}/messages",
                          json={"text": "hi", "photo_ids": [str(uuid.uuid4()) for _ in range(5)]})
    assert r.status_code == 422
