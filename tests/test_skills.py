"""Tests for the skills loader and for the Content agent's use of skills."""

from pathlib import Path
from typing import get_args

import pytest
import yaml

from buildsocial.agents.content import PLATFORM_RULES, SKILLS_DIR, ContentAgent, ContentInput
from buildsocial.harness.skills import SkillNotFoundError, load_skill, render_skills
from buildsocial.schemas import BrandProfile, Format, Platform, PostBrief

EXAMPLE_BRAND = Path(__file__).parent.parent / "examples" / "brew-house.yaml"


@pytest.fixture
def skills_root(tmp_path) -> Path:
    (tmp_path / "platforms").mkdir()
    (tmp_path / "platforms" / "x.md").write_text("## X\nBe brief.\n")
    (tmp_path / "formats").mkdir()
    (tmp_path / "formats" / "thread.md").write_text("## Thread\nNumber the posts.\n")
    return tmp_path


def test_load_skill_by_name(skills_root):
    assert load_skill(skills_root, "platforms/x") == "## X\nBe brief."


def test_render_joins_skills_in_order(skills_root):
    text = render_skills(skills_root, ["platforms/x", "formats/thread"])
    assert text == "## X\nBe brief.\n\n## Thread\nNumber the posts."


def test_missing_skill_lists_what_exists(skills_root):
    with pytest.raises(SkillNotFoundError, match=r"formats/thread.*platforms/x"):
        load_skill(skills_root, "platforms/tiktok")


def test_skill_name_cannot_escape_the_skills_folder(skills_root):
    (skills_root.parent / "secret.md").write_text("nope")
    with pytest.raises(SkillNotFoundError, match="outside"):
        load_skill(skills_root, "../secret")


# --- the real skills folder -------------------------------------------------


def test_every_platform_and_format_has_a_skill_and_rules():
    # Adding a platform/format to schemas.py without its skill file or limits fails here,
    # not at runtime in front of a user.
    for platform in get_args(Platform):
        assert load_skill(SKILLS_DIR, f"platforms/{platform}")
        assert platform in PLATFORM_RULES
    for fmt in get_args(Format):
        assert load_skill(SKILLS_DIR, f"formats/{fmt}")


def test_content_agent_loads_only_the_skills_the_brief_needs():
    brand = BrandProfile.model_validate(yaml.safe_load(EXAMPLE_BRAND.read_text()))
    agent = ContentAgent()

    def prompt(platform: str) -> str:
        brief = PostBrief(topic="monsoon special", platform=platform)
        return agent.build_system_prompt(ContentInput(brand=brand, brief=brief))

    ig, li = prompt("instagram"), prompt("linkedin")
    assert "## Platform: Instagram" in ig and "## Platform: LinkedIn" not in ig
    assert "## Platform: LinkedIn" in li and "## Platform: Instagram" not in li
    assert "## Format: text post" in ig and "## Format: text post" in li
    # The base prompt comes first and is identical for both (cache-friendly prefix).
    assert ig.startswith(agent.system_prompt.strip()) and li.startswith(agent.system_prompt.strip())
