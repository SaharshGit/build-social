"""Read an uploaded photo once with an image-reading model, and save what it found on the asset.

    asset.description  the summary (a person can edit it; a re-read never overwrites their edit)
    asset.analysis     the full reading (colours, where text fits, issues...) and what it cost

Reading is best effort: if the model fails, the upload still succeeds and analysis records the error,
so the photo can be read again later (POST /assets/{id}/read).
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from buildsocial.agents.photo_reader import PhotoReaderAgent, PhotoReading, PhotoReadInput
from buildsocial.db.models import Asset, Brand
from buildsocial.harness.agent import AgentError
from buildsocial.harness.llm.base import LLMClient, LLMError
from buildsocial.harness.llm.registry import Pricing, get_client
from buildsocial.harness.llm.types import ImageInput
from buildsocial.harness.storage import Storage
from buildsocial.harness.trace import Tracer
from buildsocial.images import for_model
from buildsocial.pipeline import StepRunner, default_pricing
from buildsocial.schemas import BrandProfile


@dataclass
class ModelDeps:
    """Which models to talk to and where traces go; tests swap in fakes."""

    client_for: Callable[[str], LLMClient] = get_client
    pricing_for: Callable[[str], Pricing] = default_pricing
    tracer_for: Callable[[str], Tracer] = field(default=lambda label: Tracer())


async def read_photo(db: AsyncSession, store: Storage, brand: Brand, asset: Asset, deps: ModelDeps) -> PhotoReading | None:
    profile = BrandProfile.model_validate(brand.profile)
    data, mime = for_model(store.read_bytes(asset.key), max_side=1536)  # enough detail to read labels
    image = ImageInput(mime_type=mime, data=data, resolution="high")
    tracer = deps.tracer_for(f"photo_{str(asset.id)[:8]}")
    run = StepRunner(tracer, deps.client_for, deps.pricing_for)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        reading = await run(PhotoReaderAgent(), PhotoReadInput(
            brand_name=profile.name, products=[p.name for p in profile.products], image=image))
    except (AgentError, LLMError) as e:
        asset.analysis = {"error": str(e)[:500], "at": now}
        await db.flush()
        return None
    finally:
        tracer.close()

    step = run.steps[0]
    asset.analysis = reading.model_dump(mode="json") | {
        "at": now, "model_cost": round(step.cost, 6), "currency": step.pricing.currency}
    if not asset.description:  # never overwrite a person's own description
        asset.description = reading.summary
    await db.flush()
    return reading
