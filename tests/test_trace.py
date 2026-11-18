"""Tests for tracing: the tracer itself, and the events the agent loop reports."""

import json

import pytest
from pydantic import BaseModel

from buildsocial.harness.agent import Agent, AgentError
from buildsocial.harness.llm.base import LLMClient, LLMError
from buildsocial.harness.llm.types import LLMResponse, Message, Usage
from buildsocial.harness.storage import LocalStorage, MemoryStorage
from buildsocial.harness.trace import FileTracer, MemoryTracer, Tracer, format_trace, latest_trace, read_trace


def price(u: Usage) -> float:  # 1 per input token, 2 per output token
    return u.input_tokens * 1 + u.output_tokens * 2


# --- the tracer itself --------------------------------------------------------


def test_file_tracer_writes_one_json_line_per_event_in_order(tmp_path):
    tracer = FileTracer(tmp_path / "traces", "content")
    tracer.event("run_start", agent="content")
    tracer.event("run_end", status="ok")

    assert tracer.path.name.endswith(f"_content_{tracer.run_id}.jsonl")
    lines = tracer.path.read_text().splitlines()
    assert [json.loads(line)["type"] for line in lines] == ["run_start", "run_end"]  # each line is valid JSON
    assert all(r["run_id"] == tracer.run_id and "ts" in r for r in read_trace(tracer.path))


def test_events_with_usage_get_a_cost():
    tracer = MemoryTracer(cost_fn=price, currency="INR")
    tracer.event("llm_call", usage=Usage(input_tokens=10, output_tokens=5))
    tracer.event("validation", errors=[])

    assert tracer.records[0]["cost"] == 20 and tracer.records[0]["currency"] == "INR"
    assert "cost" not in tracer.records[1]


def test_records_are_snapshots_and_keep_non_english_text(tmp_path):
    tracer = FileTracer(tmp_path, "x")
    messages = [Message.user("Baarish ☕ बारिश")]
    tracer.event("llm_call", messages=messages)
    messages.append(Message.user("added later"))  # must not change what was recorded

    record = read_trace(tracer.path)[0]
    assert len(record["messages"]) == 1
    assert "बारिश" in tracer.path.read_text(encoding="utf-8")  # stored readable, not as \u escapes


def test_base_tracer_records_nothing():
    Tracer().event("anything", usage=Usage())  # no error, no output


def test_latest_trace(tmp_path):
    store = MemoryStorage()
    assert latest_trace(store) is None
    store.write_text("traces/2026-01-01T10-00-00_a_1.jsonl", "")
    store.write_text("traces/2026-01-02T09-00-00_a_2.jsonl", "")
    store.write_text("traces/notes.txt", "")  # not a trace
    assert latest_trace(store) == "traces/2026-01-02T09-00-00_a_2.jsonl"


# --- events reported by the agent loop ---------------------------------------


class FakeLLM(LLMClient):
    def __init__(self, replies: list[str | Exception]):
        self.replies = list(replies)

    async def complete(self, messages, tools=None, output_schema=None, temperature=None, max_tokens=None):
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return LLMResponse(
            text=reply, stop_reason="end", usage=Usage(input_tokens=10, output_tokens=5), model="fake-model"
        )


class Topic(BaseModel):
    topic: str


class Greeting(BaseModel):
    message: str


class GreeterAgent(Agent[Topic, Greeting]):
    name = "greeter"
    output_type = Greeting
    system_prompt = "You write greetings as JSON."

    def build_user_message(self, input: Topic) -> str:
        return f"Greet {input.topic}."


VALID = '{"message": "Hello!"}'


async def test_retry_run_is_fully_traced():
    tracer = MemoryTracer(cost_fn=price, currency="INR")
    await GreeterAgent().run(Topic(topic="coffee"), FakeLLM(["not json", VALID]), tracer)

    r = tracer.records
    assert [e["type"] for e in r] == ["run_start", "llm_call", "validation", "llm_call", "validation", "run_end"]
    assert r[0]["agent"] == "greeter" and r[0]["input"] == {"topic": "coffee"}
    # Each call records exactly the prompt that was sent for that attempt.
    assert [m["role"] for m in r[1]["messages"]] == ["system", "user"]
    assert [m["role"] for m in r[3]["messages"]] == ["system", "user", "assistant", "user"]
    assert r[1]["response"]["text"] == "not json" and r[1]["cost"] == 20
    assert r[2]["errors"] and r[4]["errors"] == []
    # The end event carries the totals for the whole run.
    assert r[5]["status"] == "ok" and r[5]["attempts"] == 2
    assert r[5]["output"] == {"message": "Hello!"} and r[5]["cost"] == 40


async def test_failed_run_is_traced():
    tracer = MemoryTracer()
    with pytest.raises(AgentError):
        await GreeterAgent().run(Topic(topic="coffee"), FakeLLM(["no"] * 3), tracer)
    end = tracer.records[-1]
    assert end["type"] == "run_end" and end["status"] == "failed" and end["attempts"] == 3


async def test_api_error_is_traced_and_still_raised():
    tracer = MemoryTracer()
    with pytest.raises(LLMError):
        await GreeterAgent().run(Topic(topic="coffee"), FakeLLM([LLMError("rate limited", status_code=429)]), tracer)
    end = tracer.records[-1]
    assert end["status"] == "error" and "rate limited" in end["error"]


async def test_format_trace_is_readable():
    tracer = MemoryTracer(cost_fn=price, currency="INR")
    await GreeterAgent().run(Topic(topic="coffee"), FakeLLM(["not json", VALID]), tracer)
    text = format_trace(tracer.records)

    assert "agent: greeter" in text
    assert "ATTEMPT 1" in text and "ATTEMPT 2" in text
    assert "CHECKS FAILED (1)" in text and "CHECKS PASSED" in text
    assert "RESULT (greeter): ok | attempts: 2" in text and "INR 40.0000" in text


def test_summarize_spend_totals_per_currency_and_model(tmp_path):
    from buildsocial.harness.trace import summarize_spend

    a = FileTracer(tmp_path / "traces", "run", cost_fn=price, currency="INR")
    a.event("llm_call", usage=Usage(input_tokens=1), response={"model": "sarvam-105b"})
    a.event("llm_call", usage=Usage(input_tokens=2), response={"model": "sarvam-105b"})
    b = FileTracer(tmp_path / "traces", "run", cost_fn=lambda u: 0.5, currency="USD")
    b.event("llm_call", usage=Usage(web_searches=3), response={"model": "gemini"})
    b.event("validation", errors=[])  # not a call: ignored

    totals = summarize_spend(LocalStorage(tmp_path))
    assert totals[("INR", "sarvam-105b")] == {"calls": 2, "cost": 3, "web_searches": 0}
    assert totals[("USD", "gemini")] == {"calls": 1, "cost": 0.5, "web_searches": 3}
    assert summarize_spend(LocalStorage(tmp_path), since="2999-01-01") == {}


def test_file_tracer_uploads_to_storage_on_close_and_removes_working_copy(tmp_path):
    bucket = MemoryStorage()  # stands in for a cloud bucket
    tracer = FileTracer(tmp_path / "work", "pipeline", store=bucket)
    tracer.event("run_start", agent="content")
    assert tracer.path.exists() and bucket.list("traces/") == []  # still local while the run is going

    tracer.close()
    assert bucket.list("traces/") == [tracer.key] and not tracer.path.exists()
    assert '"type": "run_start"' in bucket.read_text(tracer.key)
    assert tracer.location() == f"memory://traces/{tracer.path.name}"
    tracer.close()  # closing twice is harmless


def test_file_tracer_without_storage_stays_a_local_file(tmp_path):
    tracer = FileTracer(tmp_path / "traces", "run")
    tracer.event("x")
    tracer.close()
    assert tracer.path.exists() and tracer.location() == str(tracer.path)
