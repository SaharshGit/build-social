"""Tests for the generic agent loop, using a fake LLM: no API key, no cost, milliseconds."""

import pytest
from pydantic import BaseModel

from buildsocial.harness.agent import Agent, AgentError, extract_json
from buildsocial.harness.llm.base import LLMClient
from buildsocial.harness.llm.types import LLMResponse, Message, Usage

# ----------------------------------------------------------------------
# Test doubles
# ----------------------------------------------------------------------


class FakeLLM(LLMClient):
    """Returns scripted replies in order and records what it was sent."""

    def __init__(self, replies: list[str]):
        self.replies = list(replies)
        self.calls: list[list[Message]] = []

    async def complete(self, messages, tools=None, output_schema=None, temperature=None, max_tokens=None):
        self.calls.append(list(messages))
        return LLMResponse(
            text=self.replies.pop(0),
            stop_reason="end",
            usage=Usage(input_tokens=10, output_tokens=5),
            model="fake-model",
        )


class Topic(BaseModel):
    topic: str


class Greeting(BaseModel):
    message: str
    language: str


class GreeterAgent(Agent[Topic, Greeting]):
    name = "greeter"
    output_type = Greeting
    system_prompt = "You write short greetings as JSON."

    def build_user_message(self, input: Topic) -> str:
        return f"Write a greeting about {input.topic}."

    def check(self, output: Greeting, input: Topic) -> list[str]:
        if len(output.message) > 20:
            return [f"message: must be at most 20 characters (got {len(output.message)})"]
        return []


VALID = '{"message": "Hello coffee!", "language": "en"}'

# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------


async def test_valid_first_try():
    llm = FakeLLM([VALID])
    result = await GreeterAgent().run(Topic(topic="coffee"), llm)

    assert result.output == Greeting(message="Hello coffee!", language="en")
    assert result.attempts == 1
    assert result.model == "fake-model"
    # The agent sent: system prompt + user message built from the input
    sent = llm.calls[0]
    assert [m.role for m in sent] == ["system", "user"]
    assert sent[1].content == "Write a greeting about coffee."


async def test_tolerates_code_fences_and_chatter():
    llm = FakeLLM([f"Sure! Here you go:\n```json\n{VALID}\n```\nEnjoy."])
    result = await GreeterAgent().run(Topic(topic="coffee"), llm)
    assert result.output.message == "Hello coffee!"


async def test_invalid_json_then_valid_retries_and_sums_usage():
    llm = FakeLLM(['{"message": "Hi", "language": ', VALID])
    result = await GreeterAgent().run(Topic(topic="coffee"), llm)

    assert result.attempts == 2
    assert result.usage == Usage(input_tokens=20, output_tokens=10)  # both attempts counted
    # The retry showed the model its own bad answer, then asked for a fix.
    retry = llm.calls[1]
    assert [m.role for m in retry] == ["system", "user", "assistant", "user"]
    assert "problems" in retry[3].content


async def test_missing_field_error_names_the_field():
    llm = FakeLLM(['{"message": "Hi"}', VALID])
    await GreeterAgent().run(Topic(topic="coffee"), llm)
    feedback = llm.calls[1][-1].content
    assert "language: Field required" in feedback


async def test_check_rule_violation_then_fixed():
    too_long = '{"message": "Hello there, wonderful coffee lovers!", "language": "en"}'
    llm = FakeLLM([too_long, VALID])
    result = await GreeterAgent().run(Topic(topic="coffee"), llm)

    assert result.attempts == 2
    assert "at most 20 characters" in llm.calls[1][-1].content


async def test_gives_up_after_max_retries():
    llm = FakeLLM(["no json here"] * 3)
    with pytest.raises(AgentError) as exc:
        await GreeterAgent().run(Topic(topic="coffee"), llm)

    err = exc.value
    assert len(err.errors_by_attempt) == 3  # 1 try + 2 retries
    assert err.usage == Usage(input_tokens=30, output_tokens=15)  # cost of the failure is still reported
    assert "did not contain a JSON object" in str(err)


def test_extract_json():
    assert extract_json('{"a": 1}') == '{"a": 1}'
    assert extract_json('text {"a": {"b": 2}} more') == '{"a": {"b": 2}}'
    assert extract_json('```json\n{"a": 1}\n```') == '{"a": 1}'
    assert extract_json("no braces") is None
    assert extract_json(None) is None
