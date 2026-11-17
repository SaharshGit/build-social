"""Saved research, so web research (paid Gemini calls) only runs when needed.

Two layers, matching the two kinds of research:

  research_cache/market/<industry>_<market>_<year>-W<week>.json
      SHARED by every brand with the same industry + market. Valid for that
      calendar week, then refreshed. Built only from MarketInput, which holds no
      brand or brief data, so it is safe to share.

  research_cache/brands/<brand>/<platform>_<topic>_<hash>.json
      PRIVATE to one brand (a topic can reveal unreleased products or plans).
      Reused for up to DEFAULT_MAX_AGE_DAYS.

Local JSON files for now; in the product this moves to the database.
"""

import hashlib
import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from buildsocial.agents.research import MarketInput, TopicInput
from buildsocial.schemas import ResearchReport

DEFAULT_DIR = Path("research_cache")
DEFAULT_MAX_AGE_DAYS = 7


class CachedResearch(BaseModel):
    saved_at: datetime
    key: dict[str, Any]  # what this research was for (readable; also helps debugging)
    report: ResearchReport

    def age_days(self, now: datetime | None = None) -> float:
        now = now or datetime.now(timezone.utc)
        return (now - self.saved_at).total_seconds() / 86400


# --- paths --------------------------------------------------------------------


def market_path(directory: Path, input: MarketInput) -> Path:
    year, week, _ = input.today.isocalendar()
    return directory / "market" / f"{slug(input.industry)}_{slug(input.market)}_{year}-W{week:02d}.json"


def topic_path(directory: Path, brand_name: str, input: TopicInput) -> Path:
    # Everything that changes what topic research should find is part of the key.
    key = json.dumps(
        [input.platform, input.topic.strip().lower(), (input.notes or "").strip(), input.industry, input.market],
        ensure_ascii=False,
    )
    digest = hashlib.sha256(key.encode()).hexdigest()[:8]
    return directory / "brands" / slug(brand_name) / f"{input.platform}_{slug(input.topic)[:40]}_{digest}.json"


# --- load / save --------------------------------------------------------------


def load_market(directory: Path, input: MarketInput) -> tuple[CachedResearch, Path] | None:
    # The week is in the file name, so a file that exists is current by definition.
    return _load(market_path(directory, input), max_age_days=None)


def save_market(directory: Path, input: MarketInput, report: ResearchReport) -> Path:
    return _save(market_path(directory, input), input.model_dump(mode="json"), report)


def load_topic(
    directory: Path, brand_name: str, input: TopicInput, max_age_days: float = DEFAULT_MAX_AGE_DAYS
) -> tuple[CachedResearch, Path] | None:
    return _load(topic_path(directory, brand_name, input), max_age_days=max_age_days)


def save_topic(directory: Path, brand_name: str, input: TopicInput, report: ResearchReport) -> Path:
    key = {"brand": brand_name, **input.model_dump(mode="json")}
    return _save(topic_path(directory, brand_name, input), key, report)


def _load(path: Path, max_age_days: float | None) -> tuple[CachedResearch, Path] | None:
    if not path.exists():
        return None
    cached = CachedResearch.model_validate_json(path.read_text(encoding="utf-8"))
    if max_age_days is not None and cached.age_days() > max_age_days:
        return None
    return cached, path


def _save(path: Path, key: dict[str, Any], report: ResearchReport) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = CachedResearch(saved_at=datetime.now(timezone.utc), key=key, report=report)
    path.write_text(entry.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return path


def slug(text: str) -> str:
    """'D2C skincare and personal care' -> 'd2c-skincare-and-personal-care'."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "x"


def week_label(day: date) -> str:
    year, week, _ = day.isocalendar()
    return f"{year}-W{week:02d}"
