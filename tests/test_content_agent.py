"""Tests for the Content agent's deterministic parts: message building and platform rules."""

from pathlib import Path

import pytest
import yaml

from buildsocial.agents.content import ContentAgent, ContentInput
from buildsocial.schemas import BrandProfile, PostBrief, PostDraft

EXAMPLE_BRAND = Path(__file__).parent.parent / "examples" / "brew-house.yaml"


@pytest.fixture
def brand() -> BrandProfile:
    return BrandProfile.model_validate(yaml.safe_load(EXAMPLE_BRAND.read_text()))


def make_input(brand: BrandProfile, platform: str = "instagram") -> ContentInput:
    return ContentInput(brand=brand, brief=PostBrief(topic="monsoon special", platform=platform))


def make_draft(**overrides) -> PostDraft:
    fields = dict(
        platform="instagram",
        hook="Baarish + cold brew? Hum judge nahi karenge.",
        body="Our monsoon special is here.",
        call_to_action="Drop by this week!",
        hashtags=["#BengaluruCafes", "#SpecialtyCoffee", "#MonsoonVibes"],
        image_idea="Rain on the café window, cold brew in front.",
        rationale="Relatable monsoon moment, brand voice.",
    )
    return PostDraft(**(fields | overrides))


def test_user_message_includes_brand_brief_and_limits(brand):
    msg = ContentAgent().build_user_message(make_input(brand))
    assert "Name: Brew House" in msg
    assert "Language: Hinglish" in msg
    assert "Topic: monsoon special" in msg
    assert "Hook: at most 125 characters" in msg
    assert "\n\n\n" not in msg  # no empty gaps from missing optional fields


def test_valid_draft_passes(brand):
    assert ContentAgent().check(make_draft(), make_input(brand)) == []


@pytest.mark.parametrize(
    "overrides, expected",
    [
        ({"platform": "linkedin"}, "platform: must be 'instagram'"),
        ({"hook": "x" * 126}, "hook: must be at most 125"),
        ({"body": "x" * 2200}, "post: whole post must be at most 2200"),
        ({"hashtags": ["#one"]}, "hashtags: need 3 to 10"),
        ({"hashtags": ["#a", "#b", "no hash"]}, "'no hash' must start with '#'"),
        ({"hashtags": ["#a", "#b", "#two words"]}, "'#two words' must start with '#'"),
        ({"hashtags": ["#a", "#b", "#"]}, "'#' must start with '#'"),
        ({"body": "Great drink. #ColdBrew"}, "body: must not contain hashtags"),
        ({"body": "Great drink.\n\nDrop by this week!"}, "body: must not repeat the call to action"),
    ],
)
def test_instagram_rule_violations(brand, overrides, expected):
    errors = ContentAgent().check(make_draft(**overrides), make_input(brand))
    assert any(expected in e for e in errors), errors


def test_body_rules_have_no_false_positives(brand):
    # 'C#' and a mid-word '#' aren't hashtags; mentioning "drop by" differently isn't a repeated CTA.
    draft = make_draft(body="We code in C# by day and brew by night. Drop by sometime, maybe.")
    assert ContentAgent().check(draft, make_input(brand)) == []


def test_linkedin_rules_differ(brand):
    agent, inp = ContentAgent(), make_input(brand, platform="linkedin")
    # LinkedIn allows zero hashtags and a longer hook...
    assert agent.check(make_draft(platform="linkedin", hashtags=[], hook="x" * 180), inp) == []
    # ...but at most 5 hashtags.
    six = [f"#tag{i}" for i in range(6)]
    assert any("need 0 to 5" in e for e in agent.check(make_draft(platform="linkedin", hashtags=six), inp))
