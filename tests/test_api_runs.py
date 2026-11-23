"""Runs + worker tests: queue a run through the API, process it with the worker, follow progress.
Fake LLMs and a throwaway database: no API calls, no cost."""

import json
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from buildsocial.db import runs as run_ops
from buildsocial.db.models import Post, Run, RunStatus
from buildsocial.harness.llm.base import LLMClient
from buildsocial.harness.llm.registry import Pricing, TokenPrices
from buildsocial.harness.llm.types import LLMResponse, Usage
from buildsocial.worker import WorkerDeps, process_one

EXAMPLES = Path(__file__).parent.parent / "examples"
INR = Pricing(currency="INR", per_1m_tokens=TokenPrices(input=10, output=25))
BRIEF = {"topic": "monsoon evenings in Bengaluru", "platform": "instagram", "goal": "engagement"}

SEARCH = "Bengaluru evenings are cool this week."
STRUCTURE = json.dumps({"summary": "s", "findings": [], "trends": []})
PLAN = json.dumps({"brief_concerns": [], "rationale": "r", "rules_check": "c", "angle": "a", "key_message": "k",
                   "finding_ids": [], "cta_direction": "visit"})
DRAFT = json.dumps({"platform": "instagram", "hook": "Baarish wali shaam?", "body": "Cosy corner, warm cup.",
                    "call_to_action": "Come say hi this week.", "hashtags": ["#BrewHouse", "#Bengaluru", "#Cafe"],
                    "image_idea": "i", "rationale": "r"})
PASS = json.dumps({"issues": [], "verdict": "pass"})


def scripted(**replies_by_model):
    """client_for(name): every client for the same model shares one queue of replies."""
    queues = {name: list(r) for name, r in replies_by_model.items()}

    class Fake(LLMClient):
        def __init__(self, name):
            self.name = name

        async def complete(self, messages, tools=None, output_schema=None, temperature=None, max_tokens=None,
                           builtin_tools=None):
            reply = queues[self.name].pop(0)
            if isinstance(reply, Exception):
                raise reply
            return LLMResponse(text=reply, stop_reason="end", usage=Usage(input_tokens=1000, output_tokens=100),
                               model=f"fake-{self.name}")

    return lambda name: Fake(name), queues


def happy_path():
    # research: topic (search + structure), then market (search + structure)
    return scripted(research=[SEARCH, STRUCTURE, SEARCH, STRUCTURE], strategy=[PLAN], content=[DRAFT],
                    reviewer=[PASS])


def worker_deps(db_maker, client_for, logs=None) -> WorkerDeps:
    @asynccontextmanager
    async def session():
        async with db_maker() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    return WorkerDeps(session=session, client_for=client_for, pricing_for=lambda n: INR,
                      log=(logs.append if logs is not None else lambda m: None))


async def create_brand(client) -> str:
    profile = yaml.safe_load((EXAMPLES / "brew-house.yaml").read_text())
    return (await client.post("/brands", json=profile)).json()["id"]


# --- API -----------------------------------------------------------------------------


async def test_start_run_returns_immediately_queued(client):
    brand_id = await create_brand(client)
    r = await client.post(f"/brands/{brand_id}/runs", json=BRIEF)
    assert r.status_code == 202
    run = r.json()
    assert run["status"] == "queued" and run["post_id"] is None and run["steps"] == []
    assert (await client.get(f"/runs/{run['id']}")).json()["status"] == "queued"


async def test_start_run_validates_brand_and_brief(client):
    assert (await client.post(f"/brands/{uuid.uuid4()}/runs", json=BRIEF)).status_code == 404
    brand_id = await create_brand(client)
    bad = BRIEF | {"platform": "tiktok"}
    r = await client.post(f"/brands/{brand_id}/runs", json=bad)
    assert r.status_code == 422 and any(e["loc"][-1] == "platform" for e in r.json()["detail"])


# --- worker ----------------------------------------------------------------------------


async def test_worker_runs_pipeline_and_saves_post(client, db_maker):
    brand_id = await create_brand(client)
    run_id = (await client.post(f"/brands/{brand_id}/runs", json=BRIEF)).json()["id"]
    client_for, queues = happy_path()
    logs: list[str] = []

    assert str(await process_one(worker_deps(db_maker, client_for, logs))) == run_id

    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] == "done" and run["current_step"] is None and run["error"] is None
    assert [s["agent"] for s in run["steps"]] == ["topic_research", "market_research", "strategy", "content",
                                                  "reviewer"]
    assert run["cost"]["INR"] > 0 and run["finished_at"]
    assert run["post_id"] is not None
    assert all(not q for q in queues.values())  # every scripted reply was used
    assert "done" in logs[-1]

    async with db_maker() as db:
        post = await db.get(Post, uuid.UUID(run["post_id"]))
        assert post.status == "pending" and post.platform == "instagram"
        assert post.draft["hook"] == "Baarish wali shaam?" and post.review["verdict"] == "pass"


async def test_progress_is_written_while_running(client, db_maker):
    """Each step's start sets current_step; each finish appends to steps (seen live by GET /runs/{id})."""
    brand_id = await create_brand(client)
    run_id = uuid.UUID((await client.post(f"/brands/{brand_id}/runs", json=BRIEF)).json()["id"])
    client_for, _ = happy_path()
    seen = []
    original = run_ops.record_progress

    async def spy(db, rid, label, summary):
        await original(db, rid, label, summary)
        run = await db.get(Run, rid)
        seen.append((label, "finished" if summary else "started", run.current_step, len(run.steps)))

    run_ops.record_progress = spy
    try:
        await process_one(worker_deps(db_maker, client_for))
    finally:
        run_ops.record_progress = original
    assert seen[0] == ("topic_research", "started", "topic_research", 0)
    assert ("strategy", "finished", "strategy", 3) in seen


async def test_failure_is_recorded_and_worker_carries_on(client, db_maker):
    brand_id = await create_brand(client)
    run_id = (await client.post(f"/brands/{brand_id}/runs", json=BRIEF)).json()["id"]
    client_for, _ = scripted(research=[RuntimeError("Gemini quota exceeded")])

    await process_one(worker_deps(db_maker, client_for))  # doesn't raise

    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] == "failed" and "Gemini quota exceeded" in run["error"] and run["post_id"] is None


async def test_empty_queue_and_oldest_first(client, db_maker):
    assert await process_one(worker_deps(db_maker, scripted()[0])) is None
    brand_id = await create_brand(client)
    first = (await client.post(f"/brands/{brand_id}/runs", json=BRIEF)).json()["id"]
    second = (await client.post(f"/brands/{brand_id}/runs", json=BRIEF)).json()["id"]
    client_for, _ = scripted(research=[RuntimeError("stop")] * 2)
    deps = worker_deps(db_maker, client_for)
    assert [str(await process_one(deps)), str(await process_one(deps))] == [first, second]
    assert await process_one(deps) is None


async def test_list_runs_filters_by_status(client, db_maker):
    brand_id = await create_brand(client)
    await client.post(f"/brands/{brand_id}/runs", json=BRIEF)
    await client.post(f"/brands/{brand_id}/runs", json=BRIEF)
    await process_one(worker_deps(db_maker, scripted(research=[RuntimeError("x")])[0]))
    assert len((await client.get("/runs")).json()) == 2
    assert len((await client.get("/runs?status=queued")).json()) == 1
    assert len((await client.get("/runs?status=failed")).json()) == 1


async def test_stuck_running_runs_are_failed_on_restart(client, db_maker):
    brand_id = await create_brand(client)
    run_id = uuid.UUID((await client.post(f"/brands/{brand_id}/runs", json=BRIEF)).json()["id"])
    async with db_maker() as db:
        run = await db.get(Run, run_id)
        run.status, run.started_at = RunStatus.RUNNING, datetime.now(timezone.utc) - timedelta(hours=1)
        await db.commit()
    async with db_maker() as db:
        assert await run_ops.fail_stale_runs(db) == 1
        await db.commit()
    run = (await client.get(f"/runs/{run_id}")).json()
    assert run["status"] == "failed" and "worker stopped" in run["error"]
