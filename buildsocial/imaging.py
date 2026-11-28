"""Making a post's image: design -> render -> measured checks -> visual check, with retries.

    attempt 1  the design agent chooses (or, on a revision that kept the same words, the earlier design is reused)
    attempt 2  if something failed: the agent again, told which layout failed and why, and must choose differently
    attempt 3  if that failed too: a plain rule-based design (text beside the photo, or a colour field)

Measured checks (code) run on every render; the visual check (a model) only when those pass, to save money.
The first attempt that passes everything wins; if none does, the least bad one is kept and marked
"needs_attention" so a person looks at it. The result is a dict stored on the post (posts.image) and the PNG in
storage. Making an image never blocks the post: the text is already saved before this runs.
"""

import time
import uuid
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.agents.designer import DesignChoice, DesignerAgent
from buildsocial.agents.visual_check import CHECK_NAMES, VisualCheckAgent, VisualCheckInput
from buildsocial.design import candidates, design_input, fallback_choice, page_spec
from buildsocial.db.models import Brand, Organization, Post
from buildsocial.harness.agent import AgentError
from buildsocial.harness.llm.base import LLMError
from buildsocial.harness.llm.types import ImageInput
from buildsocial.harness.storage import Storage
from buildsocial.images import for_model
from buildsocial.pipeline import StepRunner
from buildsocial.render.checks import failed, run_checks
from buildsocial.render.fonts import brand_font_css
from buildsocial.render.layouts import PageSpec
from buildsocial.render.renderer import Rendered, Renderer
from buildsocial.schemas import BrandProfile, PostBrief, PostDraft

MAX_ATTEMPTS = 3


class ImageError(Exception):
    """The image can't be made for a reason a person can fix (e.g. the brand has no look & feel yet)."""


class RendererLike(Protocol):
    async def render(self, spec: PageSpec, font_css: str) -> Rendered: ...


@dataclass
class ImageDeps:
    """What image making talks to; tests swap in a fake renderer and fonts."""

    store: Storage
    renderer: Callable[[], AbstractAsyncContextManager[RendererLike]] = Renderer
    font_css: Callable[[Storage, dict[str, list[int]]], Awaitable[str]] = brand_font_css


@dataclass
class _Attempt:
    choice: DesignChoice
    spec: PageSpec
    out: Rendered
    measured: list = field(default_factory=list)   # render.checks.Check list
    visual: dict | None = None                    # {"checks": [...], "read": {...}} or {"error": ...}

    @property
    def measured_failed(self) -> int:
        return len(failed(self.measured))

    @property
    def visual_failed(self) -> int:
        return sum(1 for c in (self.visual or {}).get("checks", []) if not c["passed"])

    @property
    def clean(self) -> bool:
        return self.measured_failed == 0 and self.visual_failed == 0

    def problems(self) -> list[str]:
        return ([f"{c.name}: {c.detail}" for c in failed(self.measured)]
                + [f"{c['name']}: {c['reason']}" for c in (self.visual or {}).get("checks", []) if not c["passed"]])


def _norm(text: str | None) -> str:
    return " ".join((text or "").lower().replace("’", "'").split())


def visual_result(verdict, headline: str, subline: str | None) -> dict:
    """The model's judgement, with words_exact decided by comparing what it READ with the approved words."""
    checks = [c.model_dump() for c in verdict.checks]
    wrong = []
    if _norm(verdict.headline_as_read) != _norm(headline):
        wrong.append(f"headline reads '{verdict.headline_as_read}', approved '{headline}'")
    if _norm(verdict.subline_as_read) != _norm(subline):
        wrong.append(f"subline reads '{verdict.subline_as_read}', approved '{subline}'")
    for c in checks:
        if c["name"] == "words_exact":
            c["passed"] = c["passed"] and not wrong
            if wrong:
                c["reason"] = "; ".join(wrong)
    order = {n: i for i, n in enumerate(CHECK_NAMES)}
    checks.sort(key=lambda c: order[c["name"]])
    return {"checks": checks, "read": {"headline": verdict.headline_as_read, "subline": verdict.subline_as_read}}


async def _timed_step(run: StepRunner, label: str, work):
    """Run a non-agent step (rendering) so it shows in the run's progress like the agent steps do."""
    if run.on_progress:
        await run.on_progress(label, None)
    started = time.perf_counter()
    result = await work()
    if run.on_progress:
        await run.on_progress(label, {"agent": label, "model": "playwright", "attempts": 1,
                                      "seconds": round(time.perf_counter() - started, 2), "cost": 0.0, "currency": "INR"})
    return result


async def make_image(
    db: AsyncSession, deps: ImageDeps, run: StepRunner, org: Organization, brand: Brand, post_id: uuid.UUID,
    brief: PostBrief, draft: PostDraft, *, previous: dict | None = None, other_layout: bool = False,
) -> dict:
    """Design, render and check the post's image. Returns the dict to store on the post (the PNG is saved)."""
    profile = BrandProfile.model_validate(brand.profile)
    if profile.design is None:
        raise ImageError("this brand has no look & feel yet: add its colours and fonts first (Brands > Look & feel)")
    if draft.image_text is None:
        raise ImageError("this post has no words for the image")
    design = profile.design

    cands = await candidates(db, org, brand, brief)
    base = design_input(brand, brief, draft, cands)
    css = await deps.font_css(deps.store, {design.fonts.heading.family: design.fonts.heading.weights,
                                           design.fonts.body.family: design.fonts.body.weights})

    # A revision that kept the same words keeps the same design (stable, and no model call).
    reuse: DesignChoice | None = None
    avoid_layout = None
    if previous and previous.get("choice"):
        try:
            earlier = DesignChoice.model_validate(previous["choice"])
            if other_layout:
                avoid_layout = earlier.layout
            elif not DesignerAgent().check(earlier, base):
                reuse = earlier
        except ValidationError:
            pass

    attempts: list[_Attempt] = []
    notes: list[str] = []
    seen: set[tuple] = set()
    log: list[dict] = []
    async with deps.renderer() as renderer:
        for n in range(MAX_ATTEMPTS):
            if n == 0 and reuse is not None:
                choice = reuse
            elif n < MAX_ATTEMPTS - 1:
                try:
                    choice = await run(DesignerAgent(), base.model_copy(update={
                        "avoid_layout": avoid_layout, "avoid_notes": notes}), label="design" if n == 0 else "design_retry")
                except (AgentError, LLMError) as e:
                    log.append({"step": "design", "problem": f"design agent failed: {str(e)[:200]}"})
                    choice = fallback_choice(base)
            else:
                choice = fallback_choice(base)
            key = (choice.layout, choice.photo_id, choice.text_area, choice.field_role, choice.focus, choice.scrim)
            if key in seen:
                continue
            seen.add(key)

            async def render(choice=choice):
                spec = await page_spec(db, deps.store, org, brand, brief, draft, choice, cands)
                return spec, await renderer.render(spec, css)

            spec, out = await _timed_step(run, "render", render)
            attempt = _Attempt(choice=choice, spec=spec, out=out, measured=run_checks(spec, out))
            if attempt.measured_failed == 0:
                small, mime = for_model(out.png, max_side=1024)
                try:
                    verdict = await run(VisualCheckAgent(), VisualCheckInput(
                        brand_name=profile.name, kit=[f"{c.role}: {c.name} {c.hex}" for c in design.colors],
                        fonts=[design.fonts.heading.family, design.fonts.body.family],
                        art_direction=design.art_direction, style=(brand.style or {}).get("reading"),
                        headline=draft.image_text.headline, subline=draft.image_text.subline, layout=choice.layout,
                        logo_kind="logo" if spec.logo else "wordmark", uses_photo=spec.photo is not None,
                        image=ImageInput(mime_type=mime, data=small, resolution="high")), label="visual_check")
                    attempt.visual = visual_result(verdict, draft.image_text.headline, draft.image_text.subline)
                except (AgentError, LLMError) as e:  # couldn't judge: keep the image, say it wasn't checked
                    attempt.visual = {"error": f"{type(e).__name__}: {str(e)[:200]}", "checks": []}
            attempts.append(attempt)
            if attempt.clean:
                break
            avoid_layout = choice.layout
            notes = attempt.problems()

    if not attempts:
        raise ImageError("no design could be rendered")
    best = min(attempts, key=lambda a: (a.measured_failed, a.visual_failed))  # ties: the earlier one
    version = (previous or {}).get("version", 0) + 1
    key = f"brands/{brand.id}/posts/{post_id}/image-{version}.png"
    deps.store.write_bytes(key, best.out.png, "image/png")
    return {
        "version": version, "key": key, "width": best.spec.canvas.width, "height": best.spec.canvas.height,
        "layout": best.choice.layout, "choice": best.choice.model_dump(mode="json"),
        "photo_ids": [best.choice.photo_id] if best.choice.photo_id else [],
        "words": {"headline": draft.image_text.headline, "subline": draft.image_text.subline},
        "headline_px": best.out.headline_px,
        "checks": [{"name": c.name, "passed": c.passed, "detail": c.detail} for c in best.measured],
        "visual": best.visual,
        "status": "ok" if best.clean else "needs_attention",
        "attempts": [{"layout": a.choice.layout, "photo_id": a.choice.photo_id, "problems": a.problems()}
                     for a in attempts] + log,
        "skipped_photos": [{"id": i, "why": w} for i, w in cands.skipped],
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def needs_new_image(post: Post, draft: PostDraft) -> bool:
    """After a revision: is the stored image out of date (different words, or none/failed)?"""
    image = post.image or {}
    if not image.get("key") or image.get("error") or draft.image_text is None:
        return True
    return image.get("words") != {"headline": draft.image_text.headline, "subline": draft.image_text.subline}


async def produce_post_image(
    session: Callable[[], AbstractAsyncContextManager[AsyncSession]], deps: ImageDeps, run: StepRunner,
    post_id: uuid.UUID, brief: PostBrief, *, other_layout: bool = False,
) -> None:
    """Make the image for a saved post and store it on the post. A failure is recorded on the post (the
    earlier image, if any, stays) instead of failing the run: the text is already saved and reviewable."""
    async with session() as db:
        post = await db.get(Post, post_id)
        brand = await db.get(Brand, post.brand_id)
        org = await db.get(Organization, brand.organization_id)
        draft = PostDraft.model_validate(post.draft)
        previous = post.image if post.image and post.image.get("key") else None
        try:
            post.image = await make_image(db, deps, run, org, brand, post.id, brief, draft,
                                          previous=previous, other_layout=other_layout)
        except Exception as e:
            post.image = {**(previous or {}), "error": f"{type(e).__name__}: {str(e)[:300]}"}
