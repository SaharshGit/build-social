"""Fallback: if the primary model fails in a way another model might not, ask a backup.

Only for failures that aren't our fault and might not repeat elsewhere: timeouts,
network errors, 5xx, rate limits (LLMError.retryable). A 400 (bad request) is NOT
retried on the fallback: that would hide a bug in our own code.

The reply's `model` names whichever model actually answered, and a note is added to
`http_retries`, so traces show when a fallback happened.

Cost note: run totals price each step at its agent's primary model rate, so a call
answered by the fallback is priced at the primary's rate. Fallbacks are rare, so
totals are only slightly off; the trace shows which model answered.
"""

from pydantic import BaseModel

from buildsocial.harness.llm.base import LLMClient, LLMError
from buildsocial.harness.llm.types import BuiltinTool, LLMResponse, Message, ToolSpec


class FallbackClient(LLMClient):
    def __init__(self, primary: LLMClient, fallback: LLMClient, primary_name: str, fallback_name: str):
        self.primary, self.fallback = primary, fallback
        self.primary_name, self.fallback_name = primary_name, fallback_name

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        output_schema: type[BaseModel] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        builtin_tools: list[BuiltinTool] | None = None,
    ) -> LLMResponse:
        kwargs = dict(tools=tools, output_schema=output_schema, temperature=temperature,
                      max_tokens=max_tokens, builtin_tools=builtin_tools)
        try:
            return await self.primary.complete(messages, **kwargs)
        except LLMError as e:
            if not e.retryable:
                raise
            reason = str(e)[:200]
        resp = await self.fallback.complete(messages, **kwargs)
        note = f"fallback: {self.primary_name} failed ({reason}); answered by {self.fallback_name}"
        return resp.model_copy(update={"http_retries": resp.http_retries + [note]})

    async def aclose(self) -> None:
        await self.primary.aclose()
        await self.fallback.aclose()
