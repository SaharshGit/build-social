"""Adapter for the Sarvam chat completions API.

Translates our provider-neutral types (types.py) to Sarvam's request JSON, and
Sarvam's response JSON back to our types. Nothing outside this file knows what
Sarvam's API looks like.

API reference: https://docs.sarvam.ai/api-reference/chat/chat-completions-v2
"""

import json
from typing import Any

import httpx
from pydantic import BaseModel

from buildsocial.harness.llm._http import post_json
from buildsocial.harness.llm.base import LLMClient, LLMError
from buildsocial.harness.llm.types import (
    BuiltinTool,
    LLMResponse,
    Message,
    StopReason,
    ToolCall,
    ToolSpec,
    Usage,
)

DEFAULT_BASE_URL = "https://api.sarvam.ai/v2"

# Sarvam's finish_reason -> our StopReason
_STOP_REASONS: dict[str, StopReason] = {
    "stop": "end",
    "length": "max_tokens",
    "tool_calls": "tool_calls",
}


class SarvamClient(LLMClient):
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
        default_params: extra request fields applied to every call, from models.yaml
                        (e.g. temperature, max_tokens, reasoning_effort).
                        Per-call arguments to complete() override them.
        timeout:        seconds to wait for a response; reasoning models can be slow.
        transport:      for tests only: a fake HTTP server (httpx.MockTransport).
        """
        self.model = model
        self.default_params = default_params or {}
        self.max_retries = max_retries
        # One shared HTTP client reuses connections across calls (faster than reconnecting).
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"api-subscription-key": api_key},  # Sarvam's auth header
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
        if builtin_tools:
            raise LLMError(f"Sarvam has no built-in tools (asked for {builtin_tools}); use a model that does")
        body = self._build_request(messages, tools, output_schema, temperature, max_tokens)
        data = await post_json(
            self._http, "/chat/completions", body, max_retries=self.max_retries, provider="Sarvam", model=self.model
        )
        return self._parse_response(data)

    async def aclose(self) -> None:
        await self._http.aclose()

    # ------------------------------------------------------------------
    # 1. Request: our types -> Sarvam JSON
    # ------------------------------------------------------------------

    def _build_request(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None,
        output_schema: type[BaseModel] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            **self.default_params,  # model defaults first, so per-call values below win
            "model": self.model,
            "messages": [_message_to_json(m) for m in messages],
        }
        if temperature is not None:
            body["temperature"] = temperature
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if tools:
            body["tools"] = [_tool_to_json(t) for t in tools]
            # We deliberately never send tool_choice: the default ("auto") is what we
            # want, and Sarvam rejects some tool_choice values when tools are missing.
        if output_schema is not None:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": output_schema.__name__,
                    "schema": output_schema.model_json_schema(),
                    # Not strict: Pydantic schemas don't always meet strict-mode rules.
                    # The agent loop validates with Pydantic anyway.
                    "strict": False,
                },
            }
        return body

    # ------------------------------------------------------------------
    # 2. Response: Sarvam JSON -> our types
    # ------------------------------------------------------------------

    def _parse_response(self, data: dict[str, Any]) -> LLMResponse:
        try:
            choice = data["choices"][0]
            msg = choice["message"]
            raw_usage = data.get("usage") or {}
            return LLMResponse(
                text=msg.get("content"),
                reasoning=msg.get("reasoning_content"),
                tool_calls=[_parse_tool_call(tc) for tc in msg.get("tool_calls") or []],
                stop_reason=_STOP_REASONS.get(choice.get("finish_reason"), "other"),
                usage=_parse_usage(raw_usage),
                raw_usage=raw_usage,
                model=data.get("model") or self.model,
            )
        except (KeyError, IndexError, TypeError) as e:
            raise LLMError(f"unexpected response shape from Sarvam: {e!r}: {str(data)[:500]}") from e


# ----------------------------------------------------------------------
# Helpers (module-level: they don't need the client's state)
# ----------------------------------------------------------------------


def _message_to_json(m: Message) -> dict[str, Any]:
    out: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        out["tool_calls"] = [
            {
                "id": c.id,
                "type": "function",
                # Sarvam expects arguments as a JSON *string*, not an object.
                "function": {"name": c.name, "arguments": json.dumps(c.arguments)},
            }
            for c in m.tool_calls
        ]
    if m.tool_call_id:
        out["tool_call_id"] = m.tool_call_id
    return out


def _tool_to_json(t: ToolSpec) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {"name": t.name, "description": t.description, "parameters": t.parameters},
    }


def _parse_tool_call(tc: dict[str, Any]) -> ToolCall:
    fn = tc["function"]
    raw_args = fn.get("arguments") or "{}"
    try:
        args = json.loads(raw_args)
        if not isinstance(args, dict):
            raise ValueError("arguments must be a JSON object")
    except ValueError as e:  # json.JSONDecodeError is a subclass of ValueError
        # The model made a mistake. Record it so the agent loop can tell the model.
        return ToolCall(
            id=tc["id"],
            name=fn["name"],
            arguments={},
            arguments_error=f"{e}; raw arguments: {raw_args[:300]}",
        )
    return ToolCall(id=tc["id"], name=fn["name"], arguments=args)


def _parse_usage(u: dict[str, Any]) -> Usage:
    # Sarvam's prompt_tokens INCLUDES both cache reads and cache writes; our
    # input_tokens excludes them. The *_details objects can be null, hence `or {}`.
    # Note: created_cache_tokens isn't in Sarvam's docs; found in a real response.
    prompt = u.get("prompt_tokens") or 0
    prompt_details = u.get("prompt_tokens_details") or {}
    cached = prompt_details.get("cached_tokens") or 0
    created = prompt_details.get("created_cache_tokens") or 0
    reasoning = (u.get("completion_tokens_details") or {}).get("reasoning_tokens") or 0
    return Usage(
        input_tokens=prompt - cached - created,
        cache_read_tokens=cached,
        cache_write_tokens=created,
        output_tokens=u.get("completion_tokens") or 0,  # already includes reasoning
        reasoning_tokens=reasoning,
    )
