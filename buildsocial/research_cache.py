"""Saved research, so web research (paid Gemini calls) only runs when needed.

Two layers, matching the two kinds of research:

  research_cache/market/<industry>_<market>_<year>-W<week>.json
      SHARED by every brand with the same industry + market. Valid for that
      calendar week, then refreshed. Built only from MarketInput, which holds no
      brand or brief data, so it is safe to share.

  research_cache/brands/<brand>/<platform>_<topic>_<hash>.json
      PRIVATE to one brand (a topic can reveal unreleased products or plans).
      Reused for up to DEFAULT_MAX_AGE_DAYS.

Stored through a Storage (local folder or a cloud bucket); in the product this moves to a database.
"""

import hashlib
import json
import re
from datetime import date, datetime, timezone
from typing import Any

from pydantic import BaseModel

from buildsocial.agents.research import MarketInput, TopicInput
from buildsocial.harness.storage import Storage
from buildsocial.schemas import ResearchReport

PREFIX = "research_cache"
DEFAULT_MAX_AGE_DAYS = 7


class CachedResearch(BaseModel):
    saved_at: datetime
    key: dict[str, Any]  # what this research was for (readable; also helps debugging)
    report: ResearchReport

    def age_days(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now - self.saved_at).total_seconds() / 86400


# --- keys ---------------------------------------------------------------------


def market_key(input: MarketInput) -> str:
    year, week, _ = input.today.isocalendar()
    return f"{PREFIX}/market/{slug(input.industry)}_{slug(input.market)}_{year}-W{week:02d}.json"


def topic_key(brand_name: str, input: TopicInput) -> str:
    # Everything that changes what topic research should find is part of the key.
    key = json.dumps(
        [input.platform, input.topic.strip().lower(), (input.notes or "").strip(), input.industry, input.market],
        ensure_ascii=False,
    )
    digest = hashlib.sha256(key.encode()).hexdigest()[:8]
    return f"{PREFIX}/brands/{slug(brand_name)}/{input.platform}_{slug(input.topic)[:40]}_{digest}.json"


# --- load / save --------------------------------------------------------------


def load_market(store: Storage, input: MarketInput) -> tuple[CachedResearch, str] | None:
    # The week is in the key, so an entry that exists is current by definition.
    return _load(store, market_key(input), max_age_days=None)


def save_market(store: Storage, input: MarketInput, report: ResearchReport) -> str:
    return _save(store, market_key(input), input.model_dump(mode="json"), report)


def load_topic(
    store: Storage, brand_name: str, input: TopicInput, max_age_days: float = DEFAULT_MAX_AGE_DAYS
) -> tuple[CachedResearch, str] | None:
    return _load(store, topic_key(brand_name, input), max_age_days=max_age_days)


def save_topic(store: Storage, brand_name: str, input: TopicInput, report: ResearchReport) -> str:
    key = {"brand": brand_name, **input.model_dump(mode="json")}
    return _save(store, topic_key(brand_name, input), key, report)


def _load(store: Storage, key: str, max_age_days: float | None) -> tuple[CachedResearch, str] | None:
    try:
        text = store.read_text(key)
    except FileNotFoundError:
        return None
    cached = CachedResearch.model_validate_json(text)
    if max_age_days is not None and cached.age_days() > max_age_days:
        return None
    return cached, key


def _save(store: Storage, key: str, what: dict[str, Any], report: ResearchReport) -> str:
    entry = CachedResearch(saved_at=datetime.now(timezone.utc), key=what, report=report)
    store.write_text(key, entry.model_dump_json(indent=2) + "\n")
    return key


def slug(text: str) -> str:
    """'D2C skincare and personal care' -> 'd2c-skincare-and-personal-care'."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def week_label(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"
