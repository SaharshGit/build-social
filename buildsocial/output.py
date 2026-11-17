"""Saving finished posts: a readable Markdown file plus a JSON file with everything.

    output/<brand>/<date>_<time>_<platform>_<topic>.md     what a person reads and approves
    output/<brand>/<date>_<time>_<platform>_<topic>.json   the full record: plan, facts and their
                                                           sources, review, cost, feedback

Local files for now; in the product this becomes a table in the database.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from buildsocial.pipeline import PipelineResult
from buildsocial.research_cache import slug
from buildsocial.schemas import BrandProfile, PostBrief

DEFAULT_DIR = Path("output")
Status = Literal["approved", "pending"]


def save_post(
    directory: Path,
    brand: BrandProfile,
    brief: PostBrief,
    result: PipelineResult,
    status: Status,
    feedback: list[str] | None = None,
    now: datetime | None = None,
) -> tuple[Path, Path]:
    now = now or datetime.now()
    folder = directory / slug(brand.name)
    folder.mkdir(parents=True, exist_ok=True)
    stem = f"{now:%Y-%m-%d_%H%M%S}_{brief.platform}_{slug(brief.topic)[:40]}"
    md_path, json_path = folder / f"{stem}.md", folder / f"{stem}.json"

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
    md_path.write_text("\n".join(md), encoding="utf-8")

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
    json_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return md_path, json_path
