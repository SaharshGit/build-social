"""Gemini adapter tests against a fake HTTP server, using JSON shaped like the real
responses captured on 2026-11-17 (gemini-2.5-flash-lite). No API calls."""

import json

import httpx
import pytest
from pydantic import BaseModel

import buildsocial.harness.llm._http as http_module
from buildsocial.harness.llm.base import LLMError
from buildsocial.harness.llm.gemini import GeminiClient, _inline_refs
from buildsocial.harness.llm.types import Message, ToolSpec


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(http_module, "backoff", lambda attempt: 0)


# Trimmed from the real search response.
TEXT = "* Cafes launched monsoon mocktails. * Others offer mulled wine."
SEARCH_REPLY = {
    "status": "completed",
    "model": "gemini-2.5-flash-lite",
    "usage": {"total_tokens": 177, "total_input_tokens": 16, "total_cached_tokens": 0,
              "total_output_tokens": 119, "total_tool_use_tokens": 42, "total_thought_tokens": 0},
    "steps": [
        {"type": "thought", "signature": "abc"},
        {"type": "model_output", "content": [{"type": "text", "text": TEXT, "annotations": [
            {"type": "url_citation", "start_index": 0, "end_index": 35,
             "url": "https://vertexaisearch.cloud.google.com/grounding-api-redirect/AAA", "title": "boldoutline.in"},
        ]}]},
        {"type": "google_search_call", "id": "x1", "arguments": {"queries": ["monsoon drinks Bengaluru", "cafe specials"]}},
        {"type": "google_search_result", "call_id": "x1", "result": [{"search_suggestions": "<div>...</div>"}]},
    ],
}


def client(handler, **kwargs) -> GeminiClient:
    return GeminiClient(
        api_key="test-key", model="gemini-2.5-flash-lite", transport=httpx.MockTransport(handler), **kwargs
    )


def capture(reply=SEARCH_REPLY):
    calls = []

    def handler(req):
        calls.append(req)
        return httpx.Response(200, json=reply)

    return calls, handler


class Drinks(BaseModel):
    drinks: list[str]


async def test_request_translation():
    calls, handler = capture()
    llm = client(handler, default_params={"temperature": 0.3, "max_tokens": 4096})
    await llm.complete(
        [Message.system("Be factual."), Message.user("My name is Ravi."),
         Message(role="assistant", content="Hi Ravi."), Message.user("What drinks?")],
        output_schema=Drinks,
        temperature=0.1,
    )

    req = calls[0]
    body = json.loads(req.content)
    assert str(req.url) == "https://generativelanguage.googleapis.com/v1beta/interactions"
    assert req.headers["x-goog-api-key"] == "test-key" and req.headers["api-revision"]
    assert body["store"] is False  # don't keep our data on Google's servers
    assert body["system_instruction"] == "Be factual."
    assert [s["type"] for s in body["input"]] == ["user_input", "model_output", "user_input"]
    assert body["input"][0]["content"] == [{"type": "text", "text": "My name is Ravi."}]
    assert body["generation_config"] == {"temperature": 0.1, "max_output_tokens": 4096}
    assert body["response_mime_type"] == "application/json"  # both are required together
    assert body["response_format"]["properties"]["drinks"]["type"] == "array"


async def test_search_request_has_google_search_tool_and_no_schema():
    calls, handler = capture()
    await client(handler).complete([Message.user("drinks?")], builtin_tools=["web_search"])
    body = json.loads(calls[0].content)
    assert body["tools"] == [{"type": "google_search"}]
    assert "response_format" not in body


async def test_search_response_parsing():
    _, handler = capture()
    r = await client(handler).complete([Message.user("drinks?")], builtin_tools=["web_search"])

    assert r.text == TEXT and r.stop_reason == "end" and r.model == "gemini-2.5-flash-lite"
    assert r.search_queries == ["monsoon drinks Bengaluru", "cafe specials"]
    assert len(r.citations) == 1
    c = r.citations[0]
    assert c.title == "boldoutline.in" and c.url.startswith("https://vertexaisearch")
    assert c.cited_text == TEXT[0:35]
    # Usage: tool-use tokens (search results) count as input; one search per query.
    assert r.usage.input_tokens == 16 + 42 and r.usage.output_tokens == 119
    assert r.usage.web_searches == 2
    assert r.usage.total_tokens == SEARCH_REPLY["usage"]["total_tokens"]
    assert r.raw_usage == SEARCH_REPLY["usage"]


@pytest.mark.parametrize("status, expected", [("completed", "end"), ("incomplete", "max_tokens"), ("failed", "other")])
def test_stop_reason(status, expected):
    from buildsocial.harness.llm.gemini import _stop_reason

    assert _stop_reason({"status": status}) == expected


def test_thought_tokens_count_as_output_and_reasoning():
    from buildsocial.harness.llm.gemini import _parse_usage

    u = _parse_usage({"total_input_tokens": 100, "total_cached_tokens": 40, "total_output_tokens": 50,
                      "total_thought_tokens": 30}, searches=0)
    assert (u.input_tokens, u.cache_read_tokens, u.output_tokens, u.reasoning_tokens) == (60, 40, 80, 30)


async def test_search_plus_json_is_rejected_before_calling_the_api():
    calls, handler = capture()
    with pytest.raises(LLMError, match="can't combine"):
        await client(handler).complete([Message.user("x")], output_schema=Drinks, builtin_tools=["web_search"])
    assert calls == []


async def test_function_tools_not_supported_yet():
    calls, handler = capture()
    with pytest.raises(LLMError, match="not implemented"):
        await client(handler).complete(
            [Message.user("x")], tools=[ToolSpec(name="t", description="d", parameters={})]
        )
    assert calls == []


async def test_retries_then_reports_api_error():
    replies = [httpx.Response(503), httpx.Response(400, json={"error": {"message": "bad", "code": "invalid_request"}})]
    with pytest.raises(LLMError) as exc:
        await client(lambda req: replies.pop(0)).complete([Message.user("x")])
    assert exc.value.status_code == 400 and "Gemini returned 400" in str(exc.value)


def test_inline_refs_resolves_nested_models():
    class Fact(BaseModel):
        text: str

    class Report(BaseModel):
        facts: list[Fact]
        main: Fact

    schema = _inline_refs(Report.model_json_schema())
    assert "$defs" not in schema and "$ref" not in json.dumps(schema)
    assert schema["properties"]["facts"]["items"]["properties"]["text"]["type"] == "string"
    assert schema["properties"]["main"]["properties"]["text"]["type"] == "string"
