"""Adapter for the Sarvam chat completions API.

Translates our provider-neutral types (types.py) to Sarvam's request JSON, and
Sarvam's response JSON back to our types. Nothing outside this file knows what
Sarvam's API looks like.

API reference: https://docs.sarvam.ai/api-reference/chat/chat-completions-v2
"""

import asyncio
import json
import random
from typing import Any

import httpx
from pydantic import BaseModel

from buildsocial.harness.llm.base import LLMClient, LLMError
from buildsocial.harness.llm.types import (
    LLMResponse,
    Message,
    StopReason,
    ToolCall,
    ToolSpec,
    Usage,
)

DEFAULT_BASE_URL = "https://api.sarvam.ai/v2"

# Statuses where trying again later may succeed:
# 429 = rate limited, 500/502/503/504 = server-side trouble.
# Others (400 bad request, 402 no credits, 403 bad key, 404 unknown model) won't fix
# themselves, so we fail immediately with a clear error instead of retrying.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}

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
    ):
        """
        default_params: extra request fields applied to every call, from models.yaml
                        (e.g. temperature, max_tokens, reasoning_effort).
                        Per-call arguments to complete() override them.
        timeout:        seconds to wait for a response; reasoning models can be slow.
        """
        self.model = model
        self.default_params = default_params or {}
        self.max_retries = max_retries
        # One shared HTTP client reuses connections across calls (faster than reconnecting).
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"api-subscription-key": api_key},  # Sarvam's auth header
            timeout=timeout,
        )

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        output_schema: type[BaseModel] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        body = self._build_request(messages, tools, output_schema, temperature, max_tokens)
        data = await self._post("/chat/completions", body)
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
    # 2. Sending, with retries
    # ------------------------------------------------------------------

    async def _post(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        for attempt in range(self.max_retries + 1):
            is_last = attempt == self.max_retries
            try:
                resp = await self._http.post(path, json=body)
            except httpx.TransportError as e:  # connection failed, timeout, etc.
                if is_last:
                    raise LLMError(f"network error calling Sarvam: {e!r}", retryable=True) from e
                await asyncio.sleep(_backoff(attempt))
                continue

            if resp.status_code == 200:
                return resp.json()

            retryable = resp.status_code in RETRYABLE_STATUS
            if retryable and not is_last:
                await asyncio.sleep(_retry_after(resp) or _backoff(attempt))
                continue

            raise LLMError(
                f"Sarvam returned {resp.status_code} for model {self.model}: {resp.text[:500]}",
                status_code=resp.status_code,
                retryable=retryable,
            )
        raise AssertionError("unreachable")

    # ------------------------------------------------------------------
    # 3. Response: Sarvam JSON -> our types
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


def _backoff(attempt: int) -> float:
    """Exponential backoff with jitter: ~1s, 2s, 4s ... (max 30s).

    The random jitter stops many clients from retrying at the exact same moment.
    """
    return min(2**attempt, 30) + random.uniform(0, 1)


def _retry_after(resp: httpx.Response) -> float | None:
    """Honor the server's Retry-After header (seconds) if it sent one."""
    try:
        return float(resp.headers["retry-after"])
    except (KeyError, ValueError):
        return None
