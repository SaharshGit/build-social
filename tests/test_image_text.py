"""single_image format: the writer also writes the words on the image, and every check sees them."""

import json

import pytest
import yaml

from buildsocial.agents.content import IMAGE_HEADLINE_MAX, ContentAgent, ContentInput, Revision
from buildsocial.agents.judge import JudgeAgent, JudgeInput, Judgement
from buildsocial.agents.reviewer import ReviewerAgent
from buildsocial.feedback_rules import Rule, check_rule
from buildsocial.schemas import BrandProfile, ImageText, PostBrief, Review, ReviewIssue
from tests.test_content_agent import EXAMPLE_BRAND, make_draft
from tests.test_reviewer import BRAND as MAMAEARTH, review_input

IMAGE = ImageText(headline="Winter hair, sorted", subline="Onion Hair Oil, made with onion oil and Redensyl")


def image_input(brand, format="single_image") -> ContentInput:
    return ContentInput(brand=brand, brief=PostBrief(topic="monsoon special", platform="instagram", format=format))


@pytest.fixture
def brand():
    return BrandProfile.model_validate(yaml.safe_load(EXAMPLE_BRAND.read_text()))


def test_single_image_loads_its_skill(brand):
    prompt = ContentAgent().build_system_prompt(image_input(brand))
    assert "Format: single image post" in prompt
    assert "Format: text post" not in prompt


def test_single_image_needs_image_text(brand):
    agent = ContentAgent()
    assert agent.check(make_draft(image_text=IMAGE), image_input(brand)) == []
    assert any("needs the words printed on the image" in e for e in agent.check(make_draft(), image_input(brand)))


def test_text_post_has_no_image_text(brand):
    errors = ContentAgent().check(make_draft(image_text=IMAGE), image_input(brand, format="text"))
    assert errors == ["image_text: must be null for a text post"]


@pytest.mark.parametrize("image, expected", [
    (ImageText(headline="x" * (IMAGE_HEADLINE_MAX + 1)), "image_text.headline: at most"),
    (ImageText(headline="Cold brew time", subline="y" * 91), "image_text.subline: at most"),
    (ImageText(headline="Cold brew #Bengaluru"), "no hashtags on the image"),
])
def test_image_text_limits(brand, image, expected):
    errors = ContentAgent().check(make_draft(image_text=image), image_input(brand))
    assert any(expected in e for e in errors), errors


def test_all_text_includes_the_image_but_caption_length_does_not():
    draft = make_draft(image_text=IMAGE)
    assert IMAGE.headline in draft.all_text and IMAGE.subline in draft.all_text
    assert IMAGE.headline not in draft.full_text  # the published caption, used for platform length limits
    assert "IMAGE HEADLINE: Winter hair, sorted" in draft.labelled()


def test_reviewer_sees_and_can_quote_the_image():
    draft = review_input().draft.model_copy(update={"image_text": ImageText(headline="Stops hair fall")})
    inp = review_input(draft)
    assert "IMAGE HEADLINE: Stops hair fall" in ReviewerAgent().build_user_message(inp)
    review = Review(issues=[ReviewIssue(type="risky_claim", quote="Stops hair fall", problem="p", fix="f")],
                    verdict="revise")
    assert ReviewerAgent().check(review, inp) == []


def test_judge_checks_the_image_too():
    draft = review_input().draft.model_copy(update={"image_text": ImageText(headline="Stops hair fall")})
    inp = JudgeInput(brand=MAMAEARTH, brief=review_input().brief, draft=draft)
    assert "Stops hair fall" in JudgeAgent().build_user_message(inp)
    judgement = Judgement.model_validate_json(json.dumps({
        "invented_product_claims": [], "promised_results": ["Stops hair fall"],
        "voice_reason": "r", "voice_score": 3, "usefulness_reason": "r", "usefulness_score": 3}))
    assert JudgeAgent().check(judgement, inp) == []


def test_revision_must_remove_flagged_image_text(brand):
    flagged = "Stops hair fall in winter"
    previous = make_draft(image_text=ImageText(headline=flagged))
    issue = ReviewIssue(type="risky_claim", quote=flagged, problem="p", fix="Remove it.")
    inp = image_input(brand).model_copy(update={"revision": Revision(previous=previous, issues=[issue])})
    assert f"IMAGE HEADLINE: {flagged}" in ContentAgent().build_user_message(inp)
    assert any("still in the draft" in e for e in ContentAgent().check(previous, inp))


def test_include_rules_count_words_on_the_image():
    draft = make_draft(image_text=ImageText(headline="Meet Coin"))
    assert check_rule(Rule(kind="must_include", value="Coin"), draft)[0]
    assert not check_rule(Rule(kind="must_not_include", value="coin"), draft)[0]
