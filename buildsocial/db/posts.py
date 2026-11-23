"""Post operations: turning a finished pipeline run into a row in the review queue."""

from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.db.models import Post, PostStatus, Run
from buildsocial.pipeline import PipelineResult


def facts_with_sources(result: PipelineResult) -> list[dict]:
    """The facts the post was allowed to use, each with its source links (the audit trail)."""
    return [
        {**f.model_dump(mode="json"),
         "sources": [s.model_dump(mode="json") for n in f.source_ids if (s := result.research.source(n))]}
        for f in result.plan.approved_facts(result.research)
    ]


async def create_post_from_result(db: AsyncSession, run: Run, result: PipelineResult) -> Post:
    brief = run.brief
    post = Post(
        brand_id=run.brand_id,
        run_id=run.id,
        status=PostStatus.PENDING,
        platform=brief["platform"],
        topic=brief["topic"],
        draft=result.draft.model_dump(mode="json"),
        review=result.review.model_dump(mode="json"),
        plan=result.plan.model_dump(mode="json"),
        facts=facts_with_sources(result),
        history=[{"draft": d.model_dump(mode="json"), "review": r.model_dump(mode="json")} for d, r in result.history],
        feedback=[],
    )
    db.add(post)
    await db.flush()
    await db.refresh(post)
    return post
