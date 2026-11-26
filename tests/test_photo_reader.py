"""Reading uploaded photos: images travel to the model (never into traces), readings are checked and saved."""

import json

import pytest
from pydantic import ValidationError

from buildsocial.agents.photo_reader import PhotoReaderAgent, PhotoReading, PhotoReadInput
from buildsocial.api.deps import get_model_deps
from buildsocial.harness.llm.base import LLMClient
from buildsocial.harness.llm.gemini import _message_to_step
from buildsocial.harness.llm.sarvam import _message_to_json
from buildsocial.harness.llm.types import ImageInput, LLMResponse, Message, Usage
from buildsocial.photo_reading import ModelDeps
from tests.test_api_assets import image_bytes, store, upload  # noqa: F401 (store is a fixture)
from tests.test_api_runs import INR, create_brand

READING = {
    "summary": "A glass of iced amber drink with condensation on a wooden table, soft daylight.",
    "subjects": ["glass of iced drink", "wooden table"],
    "visible_text": [],
    "colors": ["#C9772F", "#3B2418", "#F3EADB"],
    "background": "scene",
    "lighting": "soft warm daylight from the left",
    "text_space": ["top", "right"],
    "issues": [],
    "suggested_kind": "product",
    "suggested_product": "Masala Chai Cold Brew",
}
IMG = ImageInput(mime_type="image/png", data=b"\x89PNG fake bytes", resolution="high")


# --- images in messages ----------------------------------------------------------------


def test_image_bytes_never_reach_traces():
    dumped = Message.user("look", images=[IMG]).model_dump(mode="json")
    assert dumped["images"] == [{"mime_type": "image/png", "resolution": "high", "size_bytes": 15}]
    assert "fake bytes" not in json.dumps(dumped)


def test_only_user_messages_carry_images():
    with pytest.raises(ValidationError, match="only user messages"):
        Message(role="assistant", content="x", images=[IMG])


def test_adapters_send_the_image():
    step = _message_to_step(Message.user("look", images=[IMG]))
    assert step["content"][1] == {"type": "image", "data": IMG.b64(), "mime_type": "image/png", "resolution": "high"}
    sarvam = _message_to_json(Message.user("look", images=[IMG]))
    assert sarvam["content"][0] == {"type": "text", "text": "look"}
    assert sarvam["content"][1]["image_url"]["url"] == f"data:image/png;base64,{IMG.b64()}"
    assert _message_to_json(Message.user("plain"))["content"] == "plain"  # text-only stays a string


# --- the agent ------------------------------------------------------------------------------


def reader_input() -> PhotoReadInput:
    return PhotoReadInput(brand_name="Brew House", products=["Masala Chai Cold Brew"], image=IMG)


def test_agent_sends_the_photo_with_the_product_list():
    agent = PhotoReaderAgent()
    assert agent.build_images(reader_input()) == [IMG]
    assert "- Masala Chai Cold Brew" in agent.build_user_message(reader_input())


@pytest.mark.parametrize("change, error", [
    ({"suggested_product": "Cold Coffee"}, "isn't in the list"),
    ({"colors": ["#FFFFFF"]}, "give 2 to 5"),
    ({"summary": "x" * 301}, "at most 300 characters"),
])
def test_reading_rules(change, error):
    reading = PhotoReading.model_validate(READING | change)
    assert any(error in e for e in PhotoReaderAgent().check(reading, reader_input()))


def test_colours_must_be_hex():
    with pytest.raises(ValidationError):
        PhotoReading.model_validate(READING | {"colors": ["orange", "#3B2418"]})


# --- reading on upload ----------------------------------------------------------------------


class RecordingLLM(LLMClient):
    """Answers with scripted replies and remembers what it was sent."""

    def __init__(self, replies: list):
        self.replies, self.sent = replies, []

    async def complete(self, messages, tools=None, output_schema=None, temperature=None, max_tokens=None,
                       builtin_tools=None):
        self.sent.append(messages)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(text=reply, stop_reason="end", usage=Usage(input_tokens=1500, output_tokens=200),
                           model="fake-vision")


@pytest.fixture
def reader(client, store):  # noqa: F811
    llm = RecordingLLM([])
    client._transport.app.dependency_overrides[get_model_deps] = lambda: ModelDeps(
        client_for=lambda name: llm, pricing_for=lambda n: INR)
    return llm


async def test_upload_reads_the_photo(client, store, reader):  # noqa: F811
    reader.replies = [json.dumps(READING)]
    brand_id = await create_brand(client)
    a = (await upload(client, brand_id, image_bytes())).json()

    assert a["description"] == READING["summary"]
    assert a["analysis"]["text_space"] == ["top", "right"]
    assert a["analysis"]["model_cost"] == pytest.approx(0.02)  # 1500 x 10 + 200 x 25 per 1M tokens, in INR
    sent = reader.sent[0][-1]
    assert len(sent.images) == 1 and sent.images[0].mime_type == "image/jpeg"  # a model copy, not the original
    from PIL import Image
    from io import BytesIO
    with Image.open(BytesIO(sent.images[0].data)) as seen:
        assert seen.size == (400, 300)  # small uploads aren't enlarged
    assert sent.images[0].resolution == "high"


async def test_reread_keeps_a_persons_description(client, store, reader):  # noqa: F811
    reader.replies = [json.dumps(READING), json.dumps(READING | {"summary": "Model's second look."})]
    brand_id = await create_brand(client)
    a = (await upload(client, brand_id, image_bytes())).json()
    await client.patch(f"/assets/{a['id']}", json={"description": "Our cold brew, made fresh every morning."})

    again = (await client.post(f"/assets/{a['id']}/read")).json()
    assert again["description"] == "Our cold brew, made fresh every morning."  # the person's words stay
    assert again["analysis"]["summary"] == "Model's second look."


async def test_bad_readings_are_retried_then_recorded_as_an_error(client, store, reader):  # noqa: F811
    bad = json.dumps(READING | {"suggested_product": "Cold Coffee"})
    reader.replies = [bad, bad, bad]  # first try + 2 retries, all wrong
    brand_id = await create_brand(client)
    r = await upload(client, brand_id, image_bytes())
    assert r.status_code == 201  # the upload itself still works
    a = r.json()
    assert a["description"] is None and "error" in a["analysis"]
    assert "isn't in the list" in (reader.sent[1][-1].content or "")  # the retry told the model what was wrong
