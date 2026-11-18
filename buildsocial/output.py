"""Saving finished posts: a readable Markdown file plus a JSON file with everything.

    output/<brand>/<date>_<time>_<platform>_<topic>.md     what a person reads and approves
    output/<brand>/<date>_<time>_<platform>_<topic>.json   the full record: plan, facts and their
                                                           sources, review, cost, feedback

Stored through a Storage (local folder or a cloud bucket); in the product this becomes a database table.
"""

import json
from datetime import datetime
from typing import Literal

from buildsocial.harness.storage import Storage
from buildsocial.pipeline import PipelineResult
from buildsocial.research_cache import slug
from buildsocial.schemas import BrandProfile, PostBrief

PREFIX = "output"
Status = Literal["approved", "pending"]


def save_post(
    store: Storage,
    brand: BrandProfile,
    brief: PostBrief,
    result: PipelineResult,
    status: Status,
    feedback: list[str] | None = None,
    now: datetime | None = None,
) -> tuple[str, str]:
    """Save the post; returns the storage keys of the .md and .json files."""
    now = now or datetime.now()
    stem = f"{PREFIX}/{slug(brand.name)}/{now:%Y-%m-%d_%H%M%S}_{brief.platform}_{slug(brief.topic)[:40]}"
    md_key, json_key = f"{stem}.md", f"{stem}.json"

    facts = result.plan.approved_facts(result.research)
    totals = result.totals()

    md = [
        f"# {brand.name} · {brief.platform} · {status.upper()}",
        "",
        f"Topic: {brief.topic}  ",
        f"Goal: {brief.goal}  ",
        f"Saved: {now:%Y-%m-%d %H:%M}",
        "",
        "## Post",
        "",
        result.draft.full_text,
        "",
        f"**Image idea:** {result.draft.image_idea}",
        "",
        "## Open review issues",
        "",
    ]
    md += [f"- [{i.type}] \"{i.quote}\": {i.problem}" for i in result.review.issues] or ["None."]
    md += ["", "## Plan", "", f"- Angle: {result.plan.angle}", f"- Key message: {result.plan.key_message}"]
    if result.plan.brief_concerns:
        md += ["", "Concerns about the brief:"] + [f"- {c}" for c in result.plan.brief_concerns]
    md += ["", "## Facts used and their sources", ""]
    for f in facts:
        sources = ", ".join(f"[{s.title}]({s.url})" for n in f.source_ids if (s := result.research.source(n)))
        md.append(f"- {f.tagged()} ({sources or 'no source'})")
    if not facts:
        md.append("None.")
    if feedback:
        md += ["", "## Your feedback", ""] + [f"{n}. {fb}" for n, fb in enumerate(feedback, 1)]
    md += [
        "",
        "## Cost",
        "",
        ", ".join(f"{cur} {amount:.4f}" for cur, amount in totals.items()) or "nothing (all cached)",
        "",
    ]
    store.write_text(md_key, "\n".join(md))

    record = {
        "status": status,
        "saved_at": now.isoformat(timespec="seconds"),
        "brand": brand.name,
        "brief": brief.model_dump(mode="json"),
        "draft": result.draft.model_dump(mode="json"),
        "review": result.review.model_dump(mode="json"),
        "plan": result.plan.model_dump(mode="json"),
        "facts_used": [
            {**f.model_dump(mode="json"),
             "sources": [s.model_dump(mode="json") for n in f.source_ids if (s := result.research.source(n))]}
            for f in facts
        ],
        "research_origin": result.research_origin,
        "earlier_drafts": [{"draft": d.model_dump(mode="json"), "review": r.model_dump(mode="json")}
                           for d, r in result.history],
        "feedback": feedback or [],
        "steps": [s.summary() for s in result.steps],
        "cost": totals,
    }
    store.write_text(json_key, json.dumps(record, indent=2, ensure_ascii=False) + "\n")
    return md_key, json_key
