"""Background worker: processes queued runs, one at a time, outside the web API.

    uv run buildsocial worker

The API only creates runs (status "queued") and returns immediately; this process does the
slow part: run the pipeline, write progress into the runs table after every step, then save
the post (run "done") or the error (run "failed"). Several workers can run at once: each
run is claimed with a row lock, so no run is processed twice.
"""

import asyncio
import time
import uuid
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.db import runs as run_ops
from buildsocial.db.models import Brand, Post, Run, RunKind
from buildsocial.db.posts import apply_revision, create_post_from_result, result_from_post
from buildsocial.db.session import session as db_session
from buildsocial.harness.llm.base import LLMClient
from buildsocial.harness.llm.registry import Pricing, get_client
from buildsocial.harness.storage import Storage
from buildsocial.harness.trace import Tracer
from buildsocial.pipeline import default_pricing, revise_with_feedback, run_pipeline
from buildsocial.schemas import BrandProfile, PostBrief

SessionFactory = Callable[[], AbstractAsyncContextManager[AsyncSession]]


@dataclass
class WorkerDeps:
    """Everything the worker talks to; tests swap in fakes."""

    session: SessionFactory = db_session
    client_for: Callable[[str], LLMClient] = get_client
    pricing_for: Callable[[str], Pricing] = default_pricing
    cache: Storage | None = None  # research cache (the CLI passes the configured storage)
    tracer_for: Callable[[uuid.UUID], Tracer] = field(default=lambda run_id: Tracer())
    # flush=True: log lines appear immediately even when output goes to a file or a container log.
    log: Callable[[str], None] = field(default=lambda message: print(message, flush=True))


async def process_one(deps: WorkerDeps) -> uuid.UUID | None:
    """Claim and process one queued run. Returns its id, or None if the queue was empty."""
    async with deps.session() as db:  # short transaction: the claim is committed straight away
        run = await run_ops.claim_next_run(db)
        if run is None:
            return None
        run_id, brand_id, kind, brief = run.id, run.brand_id, run.kind, run.brief
        feedback, post_id = run.feedback, run.revises_post_id

    started = time.perf_counter()
    tracer = deps.tracer_for(run_id)
    trace_key = getattr(tracer, "key", None)
    deps.log(f"run {run_id}: started ({kind})")
    try:
        async with deps.session() as db:
            brand = await db.get(Brand, brand_id)
            profile = BrandProfile.model_validate(brand.profile)

        async def progress(label: str, summary: dict | None) -> None:
            async with deps.session() as db:  # own transaction: visible to GET /runs/{id} right away
                await run_ops.record_progress(db, run_id, label, summary)

        common = dict(tracer=tracer, client_for=deps.client_for, pricing_for=deps.pricing_for, on_progress=progress)
        if kind == RunKind.PIPELINE:
            result = await run_pipeline(profile, PostBrief.model_validate(brief), cache=deps.cache, **common)
            async with deps.session() as db:
                post = await create_post_from_result(db, await db.get(Run, run_id), result)
                await run_ops.finish_run(db, run_id, trace_key)
        elif kind == RunKind.REVISION:
            async with deps.session() as db:
                saved = result_from_post(await db.get(Post, post_id))
            revised = await revise_with_feedback(saved, profile, PostBrief.model_validate(brief), feedback, **common)
            async with deps.session() as db:  # the SAME post gets the new draft; the old one goes to history
                post = await apply_revision(db, await db.get(Post, post_id), revised, feedback)
                await run_ops.finish_run(db, run_id, trace_key)
        else:
            raise NotImplementedError(f"'{kind}' runs aren't supported")
        deps.log(f"run {run_id}: done in {time.perf_counter() - started:.0f}s -> post {post.id}")
    except Exception as e:  # any failure is recorded on the run; the worker keeps going
        async with deps.session() as db:
            await run_ops.fail_run(db, run_id, f"{type(e).__name__}: {e}", trace_key)
        deps.log(f"run {run_id}: FAILED after {time.perf_counter() - started:.0f}s: {type(e).__name__}: {e}")
    finally:
        tracer.close()  # e.g. upload the trace to the bucket
    return run_id


async def run_forever(deps: WorkerDeps, poll_seconds: float = 2.0, stale_after: timedelta = timedelta(minutes=15)):
    async with deps.session() as db:
        stale = await run_ops.fail_stale_runs(db, stale_after)
    if stale:
        deps.log(f"marked {stale} stuck run(s) as failed (their worker stopped mid-run)")
    deps.log(f"worker ready: checking for queued runs every {poll_seconds:g}s (Ctrl+C to stop)")
    while True:
        if await process_one(deps) is None:
            await asyncio.sleep(poll_seconds)  # queue empty: wait a little before checking again
