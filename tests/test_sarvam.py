"""Sarvam adapter tests against a fake HTTP server (httpx.MockTransport): no API calls."""

import json

import httpx
import pytest
from pydantic import BaseModel

import buildsocial.harness.llm._http as http_module
from buildsocial.harness.llm.base import LLMError
from buildsocial.harness.llm.sarvam import SarvamClient, _parse_usage
from buildsocial.harness.llm.types import Message, ToolCall, ToolSpec


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http_module, "backoff", lambda attempt: 0)


class Post(BaseModel):
    hook: str


TOOL_CALL_REPLY = {
    "model": "sarvam-105b",
    "choices": [{"finish_reason": "tool_calls", "message": {
        "content": None,
        "reasoning_content": "I should search first.",
        "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "web_search", "arguments": '{"q": "coffee"}'}},
            {"id": "c2", "type": "function", "function": {"name": "web_search", "arguments": "{broken"}},
        ],
    }}],
    "usage": {"prompt_tokens": 1000, "completion_tokens": 150,
              "prompt_tokens_details": {"cached_tokens": 800},
              "completion_tokens_details": {"reasoning_tokens": 100}},
}


def client(handler, **kwargs) -> SarvamClient:
    return SarvamClient(api_key="test-key", model="sarvam-105b", transport=httpx.MockTransport(handler), **kwargs)


async def test_request_and_response_translation():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=TOOL_CALL_REPLY)

    llm = client(handler, default_params={"temperature": 0.7, "max_tokens": 4096})
    history = [
        Message.system("sys"),
        Message.user("hi"),
        Message(role="assistant", tool_calls=[ToolCall(id="c0", name="t", arguments={"a": 1})]),
        Message.tool_result("c0", "result"),
    ]
    r = await llm.complete(
        history,
        tools=[ToolSpec(name="web_search", description="search", parameters={"type": "object"})],
        output_schema=Post,
        temperature=0.2,
    )

    req = calls[0]
    sent = json.loads(req.content)
    assert str(req.url) == "https://api.sarvam.ai/v2/chat/completions"
    assert req.headers["api-subscription-key"] == "test-key"
    assert sent["temperature"] == 0.2 and sent["max_tokens"] == 4096  # per-call wins, defaults kept
    assert "tool_choice" not in sent  # Sarvam quirk
    assert sent["response_format"]["type"] == "json_schema"
    assert sent["messages"][2]["tool_calls"][0]["function"]["arguments"] == '{"a": 1}'  # sent as a string

    assert r.stop_reason == "tool_calls" and r.reasoning == "I should search first."
    assert r.tool_calls[0].arguments == {"q": "coffee"}
    assert r.tool_calls[1].arguments == {} and "raw arguments: {broken" in r.tool_calls[1].arguments_error
    assert r.usage.input_tokens == 200 and r.usage.cache_read_tokens == 800 and r.usage.reasoning_tokens == 100


def test_usage_includes_undocumented_cache_writes():
    raw = {"prompt_tokens": 37, "completion_tokens": 1495, "total_tokens": 1532,
           "completion_tokens_details": {"reasoning_tokens": 1479},
           "prompt_tokens_details": {"cached_tokens": 0, "created_cache_tokens": 32}}
    u = _parse_usage(raw)
    assert (u.input_tokens, u.cache_write_tokens) == (5, 32)
    assert u.total_tokens == raw["total_tokens"]
    assert _parse_usage({"prompt_tokens": 10, "prompt_tokens_details": None}).input_tokens == 10


async def test_retries_503_then_succeeds():
    replies = [httpx.Response(503, text="overloaded"), httpx.Response(200, json=TOOL_CALL_REPLY)]
    r = await client(lambda req: replies.pop(0)).complete([Message.user("hi")])
    assert r.model == "sarvam-105b" and not replies


async def test_403_fails_immediately_without_retry():
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(403, text='{"error": "invalid key"}')

    with pytest.raises(LLMError) as exc:
        await client(handler).complete([Message.user("hi")])
    assert exc.value.status_code == 403 and not exc.value.retryable and len(calls) == 1


async def test_builtin_tools_are_rejected_clearly():
    with pytest.raises(LLMError, match="no built-in tools"):
        await client(lambda req: httpx.Response(200, json={})).complete([Message.user("hi")], builtin_tools=["web_search"])
