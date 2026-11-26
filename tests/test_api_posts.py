"""Review queue tests: list, view, approve, reject, revise (through the worker). No API calls."""

import json
import uuid

import pytest

from buildsocial.worker import process_one
from tests.test_api_runs import BRIEF, DRAFT, PASS, create_brand, happy_path, scripted, worker_deps

# The feedback check's reply: one measurable request (code decides "done" from the post itself).
WINDOW_SEATS_DONE = json.dumps({"checks": [{"request": "Mention the window seats", "rule_kind": "must_include",
                                            "rule_value": "window seat", "reason": "added", "evidence": "", "done": True}]})

REVISED = json.dumps(json.loads(DRAFT) | {"body": "Cosy corner, warm cup, and a window seat."})


@pytest.fixture
async def pending_post(client, db_maker) -> str:
    """A brand, a run through the worker, and the resulting pending post's id."""
    brand_id = await create_brand(client)
    run_id = (await client.post(f"/brands/{brand_id}/runs", json=BRIEF)).json()["id"]
    await process_one(worker_deps(db_maker, happy_path()[0]))
    return (await client.get(f"/runs/{run_id}")).json()["post_id"]


async def test_review_queue_lists_pending_posts(client, pending_post):
    queue = (await client.get("/posts?status=pending")).json()
    assert [p["id"] for p in queue] == [pending_post]
    assert queue[0]["hook"] == "Baarish wali shaam?" and queue[0]["open_issues"] == 0 and queue[0]["revisions"] == 0
    assert (await client.get("/posts?status=approved")).json() == []


async def test_get_post_has_everything_a_reviewer_needs(client, pending_post):
    p = (await client.get(f"/posts/{pending_post}")).json()
    assert p["status"] == "pending" and p["platform"] == "instagram"
    assert p["full_text"].startswith("Baarish wali shaam?") and "#BrewHouse" in p["full_text"]
    assert p["review"]["verdict"] == "pass" and p["plan"]["angle"] == "a"
    assert (await client.get(f"/posts/{uuid.uuid4()}")).status_code == 404


async def test_approve(client, pending_post):
    r = await client.post(f"/posts/{pending_post}/approve")
    assert r.status_code == 200 and r.json()["status"] == "approved" and r.json()["decided_at"]
    again = await client.post(f"/posts/{pending_post}/approve")
    assert again.status_code == 409 and "already approved" in again.json()["detail"]


async def test_reject_keeps_the_reason(client, pending_post):
    r = await client.post(f"/posts/{pending_post}/reject", json={"reason": "Off-brand tone"})
    assert r.json()["status"] == "rejected"
    assert r.json()["feedback"][-1]["kind"] == "rejected" and r.json()["feedback"][-1]["text"] == "Off-brand tone"


async def test_revise_updates_the_same_post_and_keeps_history(client, db_maker, pending_post):
    r = await client.post(f"/posts/{pending_post}/revise", json={"feedback": "Mention the window seats."})
    assert r.status_code == 202
    run = r.json()
    assert run["kind"] == "revision" and run["status"] == "queued" and run["post_id"] is None

    client_for, queues = scripted(content=[REVISED], reviewer=[PASS], feedback_check=[WINDOW_SEATS_DONE])
    await process_one(worker_deps(db_maker, client_for))

    run = (await client.get(f"/runs/{run['id']}")).json()
    assert run["status"] == "done" and run["post_id"] == pending_post  # the SAME post
    assert [s["agent"] for s in run["steps"]] == ["feedback_revision_1", "feedback_review_1", "feedback_check_1"]
    assert all(not q for q in queues.values())

    p = (await client.get(f"/posts/{pending_post}")).json()
    assert p["status"] == "pending" and "window seat" in p["draft"]["body"]
    assert len(p["history"]) == 1 and p["history"][0]["feedback"] == "Mention the window seats."
    assert p["history"][0]["draft"]["body"] == "Cosy corner, warm cup."  # the old draft is kept
    assert p["feedback"][-1] == p["feedback"][-1] | {"kind": "revise", "text": "Mention the window seats."}
    # The request was checked by code (a must_include rule) and is shown as done.
    assert p["feedback"][-1]["checks"] == [{"request": "Mention the window seats", "done": True, "detail": "found",
                                            "measured": True, "rule_kind": "must_include",
                                            "rule_value": "window seat"}]


async def test_revision_prompt_carries_the_feedback_and_original_plan(client, db_maker, pending_post):
    await client.post(f"/posts/{pending_post}/revise", json={"feedback": "Mention the window seats."})
    seen = []
    client_for, _ = scripted(content=[REVISED], reviewer=[PASS], feedback_check=[WINDOW_SEATS_DONE])

    def spying_client_for(name):
        llm = client_for(name)
        original = llm.complete

        async def complete(messages, **kw):
            seen.append((name, messages[1].content))
            return await original(messages, **kw)

        llm.complete = complete
        return llm

    await process_one(worker_deps(db_maker, spying_client_for))
    content_prompt = next(text for name, text in seen if name == "content")
    assert "Mention the window seats." in content_prompt and "# Plan (from the strategist" in content_prompt


async def test_one_revision_at_a_time_and_only_for_pending(client, pending_post):
    assert (await client.post(f"/posts/{pending_post}/revise", json={"feedback": "Shorter."})).status_code == 202
    busy = await client.post(f"/posts/{pending_post}/revise", json={"feedback": "Even shorter."})
    assert busy.status_code == 409 and "already in progress" in busy.json()["detail"]
    assert (await client.post(f"/posts/{pending_post}/approve")).status_code == 409  # wait for the revision


async def test_cannot_revise_a_decided_post(client, pending_post):
    await client.post(f"/posts/{pending_post}/approve")
    r = await client.post(f"/posts/{pending_post}/revise", json={"feedback": "Shorter."})
    assert r.status_code == 409 and "only pending posts" in r.json()["detail"]


async def test_feedback_must_say_something(client, pending_post):
    assert (await client.post(f"/posts/{pending_post}/revise", json={"feedback": ""})).status_code == 422
