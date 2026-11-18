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


def test_linkedin_bans_instagram_link_in_bio(brand):
    inp = make_input(brand, platform="linkedin")
    draft = make_draft(platform="linkedin", hashtags=[], call_to_action="Visit the link in bio to read more.")
    assert any("'link in bio' doesn't exist on linkedin" in e for e in ContentAgent().check(draft, inp))
    # Fine on Instagram.
    ig = make_draft(call_to_action="Visit the link in bio to read more.")
    assert ContentAgent().check(ig, make_input(brand)) == []


def test_strategy_plans_with_the_platform_skill(brand):
    from buildsocial.agents.strategy import StrategyAgent, StrategyInput
    from buildsocial.schemas import ResearchReport

    research = ResearchReport(summary="s", findings=[], trends=[], search_queries=[], sources=[])
    prompt = StrategyAgent().build_system_prompt(StrategyInput(brand=brand, brief=PostBrief(
        topic="t", platform="linkedin"), research=research))
    assert "## Platform: LinkedIn" in prompt and "no \"link in bio\"" in prompt


def test_post_must_name_the_product_the_brief_is_about(brand):
    brief = PostBrief(topic="new Masala Chai Cold Brew", platform="instagram")
    inp = ContentInput(brand=brand, brief=brief)
    no_name = make_draft(hook="Chai, but cold.", body="Our new drink is here.")
    assert any("must name it" in e for e in ContentAgent().check(no_name, inp))
    named = make_draft(hook="Chai, but cold.", body="Our new Masala Chai Cold Brew is here.")
    assert ContentAgent().check(named, inp) == []
    # A topic that names no listed product has no such requirement.
    other = ContentInput(brand=brand, brief=PostBrief(topic="monsoon in Bengaluru", platform="instagram"))
    assert not any("must name it" in e for e in ContentAgent().check(no_name, other))
