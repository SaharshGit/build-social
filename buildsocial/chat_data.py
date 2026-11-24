"""Read-only data tools the chat can use to answer questions ("how many posts are pending?").

The chat agent never writes SQL. It fills a DataQuery (which tool + filters); the fixed
functions here run it, always limited to the asking organization, and return a small
JSON-able result. The reply is then written from that result only (agents/chat.py).
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.db.models import Brand, Organization, Post, Run, RunStatus
from buildsocial.schemas import BrandProfile, Platform

Tool = Literal["count_posts", "list_posts", "run_stats", "spend", "brand_info"]

TOOLS_HELP = """\
- count_posts: how many posts, per status (pending / approved / rejected). Filters: brand, status, platform, days.
- list_posts: the most recent posts (up to 10) with brand, platform, status, topic, date. Filters: brand, status, platform, days.
- run_stats: post-creation runs: how many, per status (queued / running / done / failed), average time, recent failures. Filters: brand, days.
- spend: LLM cost of creating and revising posts, per currency and per brand. Filters: brand, days.
- brand_info: one brand's profile (description, audience, voice, pillars, products) and its post counts. Needs brand."""


class DataQuery(BaseModel):
    tool: Tool
    brand: str | None = Field(default=None, description="Exact brand name, or empty for all brands")
    status: Literal["pending", "approved", "rejected"] | None = Field(default=None, description="Posts only")
    platform: Platform | None = None
    days: int | None = Field(default=None, ge=1, le=365, description="Only the last N days; empty = all time")


async def run_query(db: AsyncSession, org: Organization, q: DataQuery) -> dict[str, Any]:
    brands = list((await db.execute(select(Brand).where(Brand.organization_id == org.id))).scalars())
    brand = next((b for b in brands if q.brand and b.name.lower() == q.brand.lower()), None)
    if q.brand and brand is None:
        return {"error": f"no brand named {q.brand!r}", "brands": [b.name for b in brands]}
    names = {b.id: b.name for b in brands}
    since = datetime.now(timezone.utc) - timedelta(days=q.days) if q.days else None
    scope = {"brand": brand.name if brand else "all brands", "period": f"last {q.days} days" if q.days else "all time"}

    def posts_filter(stmt, with_status: bool = True):
        stmt = stmt.join(Brand, Post.brand_id == Brand.id).where(Brand.organization_id == org.id)
        if brand:
            stmt = stmt.where(Post.brand_id == brand.id)
        if q.platform:
            stmt = stmt.where(Post.platform == q.platform)
        if since:
            stmt = stmt.where(Post.created_at >= since)
        if with_status and q.status:
            stmt = stmt.where(Post.status == q.status)
        return stmt

    def runs_filter(stmt):
        stmt = stmt.join(Brand, Run.brand_id == Brand.id).where(Brand.organization_id == org.id)
        if brand:
            stmt = stmt.where(Run.brand_id == brand.id)
        if since:
            stmt = stmt.where(Run.created_at >= since)
        return stmt

    if q.tool == "count_posts":
        rows = await db.execute(posts_filter(select(Post.status, func.count()), with_status=False)
                                .group_by(Post.status))
        by_status = {s: 0 for s in ("pending", "approved", "rejected")} | dict(rows.all())
        total = by_status[q.status] if q.status else sum(by_status.values())
        extra = {"platform": q.platform} if q.platform else {}
        return {**scope, **extra, "status": q.status or "any", "total": total, "by_status": by_status}

    if q.tool == "list_posts":
        rows = list((await db.execute(posts_filter(select(Post)).order_by(Post.created_at.desc()).limit(11)))
                    .scalars())
        posts = [{"id": str(p.id), "brand": names[p.brand_id], "platform": p.platform, "status": p.status,
                  "topic": p.topic, "created": p.created_at.date().isoformat(),
                  "open_issues": len(p.review.get("issues", []))} for p in rows[:10]]
        return {**scope, "status": q.status or "any", "shown": len(posts), "more_exist": len(rows) > 10,
                "posts": posts}

    if q.tool == "run_stats":
        runs = list((await db.execute(runs_filter(select(Run)).order_by(Run.created_at.desc()))).scalars())
        by_status = {s.value: 0 for s in RunStatus}
        for r in runs:
            by_status[r.status] = by_status.get(r.status, 0) + 1
        times = [(r.finished_at - r.started_at).total_seconds() for r in runs
                 if r.status == RunStatus.DONE and r.started_at and r.finished_at]
        failures = [{"brand": names[r.brand_id], "topic": r.brief.get("topic"), "date": r.created_at.date().isoformat(),
                     "error": (r.error or "")[:150]} for r in runs if r.status == RunStatus.FAILED][:3]
        return {**scope, "total": len(runs), "by_status": by_status,
                "average_seconds": round(sum(times) / len(times)) if times else None, "recent_failures": failures}

    if q.tool == "spend":
        runs = list((await db.execute(runs_filter(select(Run)))).scalars())
        total: dict[str, float] = {}
        per_brand: dict[str, dict[str, float]] = {}
        for r in runs:
            for currency, cost in (r.cost or {}).items():
                total[currency] = total.get(currency, 0.0) + cost
                pb = per_brand.setdefault(names[r.brand_id], {})
                pb[currency] = pb.get(currency, 0.0) + cost
        return {**scope, "runs": len(runs), "total_cost": _round(total),
                "per_brand": {b: _round(c) for b, c in per_brand.items()},
                "note": "cost of creating and revising posts; chat replies are not included"}

    # brand_info
    if brand is None:
        return {"error": "which brand?", "brands": [b.name for b in brands]}
    p = BrandProfile.model_validate(brand.profile)
    rows = await db.execute(select(Post.status, func.count()).where(Post.brand_id == brand.id).group_by(Post.status))
    return {"name": p.name, "industry": p.industry, "market": p.market, "description": p.description,
            "audience": p.audience, "voice": p.voice, "language": p.language, "content_pillars": p.content_pillars,
            "products": [x.name for x in p.products], "posts_by_status": dict(rows.all())}


def _round(costs: dict[str, float]) -> dict[str, float]:
    return {c: round(v, 2) for c, v in costs.items()}
