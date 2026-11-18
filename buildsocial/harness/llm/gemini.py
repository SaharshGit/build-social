"""Adapter for the Gemini Interactions API.

Translates our provider-neutral types (types.py) to Gemini's request JSON, and
Gemini's response JSON back to our types. Nothing outside this file knows what
Gemini's API looks like.

Written from real responses (probe on 2026-11-17, gemini-2.5-flash-lite):
- Conversation history is a list of "steps" (user_input / model_output); the
  system prompt is a separate `system_instruction` string.
- JSON output needs BOTH response_format (the schema) and response_mime_type.
- Google Search + JSON output in one call is rejected by the API, so callers must
  do search and structuring in separate calls.
- Citations are url_citation annotations on the output text; their URLs are
  Google redirect links and the title is the source's domain.
- Usage has total_tool_use_tokens (search results fed to the model), billed as input.

API docs: https://aistudio.google.com/docs/interactions
"""

import copy
from typing import Any

import httpx
from pydantic import BaseModel

from buildsocial.harness.llm._http import post_json
from buildsocial.harness.llm.base import LLMClient, LLMError
from buildsocial.harness.llm.types import (
    BuiltinTool,
    Citation,
    LLMResponse,
    Message,
    StopReason,
    ToolSpec,
    Usage,
)

DEFAULT_BASE_URL = "https://generativelanguage.googleapis.com/v1beta"
API_REVISION = "2026-05-20"  # pins the response format we wrote this adapter against

# Our built-in tool names -> Gemini's tool objects
_BUILTIN_TOOLS: dict[str, dict[str, Any]] = {
    "web_search": {"type": "google_search"},
}

# Our parameter names -> Gemini generation_config names
_GENERATION_PARAMS = {"temperature": "temperature", "max_tokens": "max_output_tokens"}


class GeminiClient(LLMClient):
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = DEFAULT_BASE_URL,
        default_params: dict[str, Any] | None = None,
        timeout: float = 120.0,
        max_retries: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ):
        """
        default_params: settings from models.yaml applied to every call (temperature,
                        max_tokens, or any other Gemini generation_config field).
        transport:      for tests only: a fake HTTP server (httpx.MockTransport).
        """
        self.model = model
        self.default_params = default_params or {}
        self.max_retries = max_retries
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"x-goog-api-key": api_key, "Api-Revision": API_REVISION},
            timeout=timeout,
            transport=transport,
        )

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        output_schema: type[BaseModel] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        builtin_tools: list[BuiltinTool] | None = None,
    ) -> LLMResponse:
        body = self._build_request(messages, tools, output_schema, temperature, max_tokens, builtin_tools)
        retries: list[str] = []
        data = await post_json(
            self._http, "/interactions", body,
            max_retries=self.max_retries, provider="Gemini", model=self.model, retry_log=retries,
        )
        return self._parse_response(data).model_copy(update={"http_retries": retries})

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # 1. Request: our types -> Gemini JSON
    # ------------------------------------------------------------------

    def _build_request(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        output_schema: type[BaseModel] | None,
        temperature: float | None,
        max_tokens: int | None,
        builtin_tools: list[BuiltinTool] | None,
    ) -> dict[str, Any]:
        if tools:
            # Our own function tools aren't needed yet (prototype 1 uses built-in search only).
            raise LLMError("function tools are not implemented in the Gemini adapter yet")
        if builtin_tools and output_schema is not None:
            raise LLMError(
                "Gemini can't combine built-in tools with JSON output in one call; "
                "search first, then structure the result in a second call"
            )

        system_parts = [m.content for m in messages if m.role == "system" and m.content]
        steps = [_message_to_step(m) for m in messages if m.role != "system"]

        body: dict[str, Any] = {
            "model": self.model,
            "input": steps,
            # Don't keep our conversations on Google's servers; we always send full history.
            "store": False,
        }
        if system_parts:
            body["system_instruction"] = "\n\n".join(system_parts)

        config = {_GENERATION_PARAMS.get(k, k): v for k, v in self.default_params.items()}
        if temperature is not None:
            config["temperature"] = temperature
        if max_tokens is not None:
            config["max_output_tokens"] = max_tokens
        if config:
            body["generation_config"] = config

        if builtin_tools:
            unknown = [t for t in builtin_tools if t not in _BUILTIN_TOOLS]
            if unknown:
                raise LLMError(f"Gemini adapter doesn't support built-in tools {unknown}")
            body["tools"] = [_BUILTIN_TOOLS[t] for t in builtin_tools]

        if output_schema is not None:
            body["response_mime_type"] = "application/json"
            body["response_format"] = _inline_refs(output_schema.model_json_schema())
        return body

    # ------------------------------------------------------------------
    # 2. Response: Gemini JSON -> our types
    # ------------------------------------------------------------------

    def _parse_response(self, data: dict[str, Any]) -> LLMResponse:
        try:
            steps = data.get("steps") or []
            text_parts: list[str] = []
            citations: list[Citation] = []
            queries: list[str] = []

            for step in steps:
                kind = step.get("type")
                if kind == "model_output":
                    for block in step.get("content") or []:
                        if block.get("type") != "text":
                            continue
                        text = block.get("text") or ""
                        for ann in block.get("annotations") or []:
                            if ann.get("type") == "url_citation" and ann.get("url"):
                                start, end = ann.get("start_index"), ann.get("end_index")
                                cited = text[start:end] if start is not None and end is not None else None
                                citations.append(Citation(url=ann["url"], title=ann.get("title"), cited_text=cited))
                        text_parts.append(text)
                elif kind == "google_search_call":
                    queries += (step.get("arguments") or {}).get("queries") or []

            if not text_parts:
                # Seen once with status "completed" and real token usage but no output text.
                # Never pass an empty answer on as if it were a real one; let the caller retry.
                step_types = [s.get("type") for s in steps]
                raise LLMError(
                    f"Gemini returned no text (status={data.get('status')}, steps={step_types}, "
                    f"usage={data.get('usage')})",
                    retryable=True,
                )

            raw_usage = data.get("usage") or {}
            return LLMResponse(
                text="".join(text_parts) or None,
                citations=citations,
                search_queries=queries,
                stop_reason=_stop_reason(data),
                usage=_parse_usage(raw_usage, searches=len(queries)),
                raw_usage=raw_usage,
                model=data.get("model") or self.model,
            )
        except (KeyError, TypeError, AttributeError) as e:
            raise LLMError(f"unexpected response shape from Gemini: {e!r}: {str(data)[:500]}") from e


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


def _message_to_step(m: Message) -> dict[str, Any]:
    if m.role == "user":
        step_type = "user_input"
    elif m.role == "assistant":
        if m.tool_calls:
            raise LLMError("function tool calls are not implemented in the Gemini adapter yet")
        step_type = "model_output"
    else:
        raise LLMError(f"Gemini adapter can't send '{m.role}' messages yet")
    return {"type": step_type, "content": [{"type": "text", "text": m.content or ""}]}


def _stop_reason(data: dict[str, Any]) -> StopReason:
    status = data.get("status")
    if status == "completed":
        return "end"
    # A reply cut off by max_output_tokens comes back as "incomplete" with no reason
    # field (seen 2026-11-17). Hitting the token limit is the usual cause, so treat it so.
    if status == "incomplete":
        return "max_tokens"
    return "other"


def _parse_usage(u: dict[str, Any], searches: int) -> Usage:
    # total_input_tokens INCLUDES cached tokens; ours excludes them.
    # total_tool_use_tokens = search results the model read, billed as input.
    # total_thought_tokens is reported separately from total_output_tokens (Gemini
    # convention); ours counts reasoning inside output_tokens, so add it in.
    total_in = u.get("total_input_tokens") or 0
    cached = u.get("total_cached_tokens") or 0
    tool_use = u.get("total_tool_use_tokens") or 0
    thoughts = u.get("total_thought_tokens") or 0
    return Usage(
        input_tokens=total_in - cached + tool_use,
        cache_read_tokens=cached,
        output_tokens=(u.get("total_output_tokens") or 0) + thoughts,
        reasoning_tokens=thoughts,
        web_searches=searches,
    )


def _inline_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Replace "$ref": "#/$defs/Name" with the definition itself and drop "$defs".

    Pydantic puts nested models under $defs and links to them with $ref. Inlining
    gives a single self-contained schema, which schema-mode APIs handle most reliably.
    """
    schema = copy.deepcopy(schema)
    defs = schema.pop("$defs", {})

    def resolve(node: Any) -> Any:
        if isinstance(node, dict):
            ref = node.get("$ref")
            if isinstance(ref, str) and ref.startswith("#/$defs/"):
                return resolve(defs[ref.removeprefix("#/$defs/")])
            return {k: resolve(v) for k, v in node.items()}
        if isinstance(node, list):
            return [resolve(v) for v in node]
        return node

    return resolve(schema)
