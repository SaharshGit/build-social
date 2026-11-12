"""The contract every LLM provider adapter must follow.

Agents depend on LLMClient, never on a concrete adapter. Swapping providers
means building a different adapter; agent code doesn't change.
"""

from abc import ABC, abstractmethod

from pydantic import BaseModel

from buildsocial.harness.llm.types import LLMResponse, Message, ToolSpec


class LLMError(Exception):
    """Any failure talking to an LLM provider, in a provider-neutral form."""

    def __init__(self, message: str, *, status_code: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status_code = status_code  # HTTP status, if there was one
        self.retryable = retryable  # True if trying again later might succeed


class LLMClient(ABC):
    """Base class for provider adapters (OpenAI-compatible, Anthropic, ...)."""

    @abstractmethod
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSpec] | None = None,
        output_schema: type[BaseModel] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResponse:
        """Send a conversation to the model and return its reply in our format.

        messages:      full conversation history (we always send all of it)
        tools:         tools the model may call this turn
        output_schema: if given, ask the model for JSON matching this schema
                       (validation and retries happen in the agent loop, not here)
        temperature,
        max_tokens:    None = use the model's defaults from models.yaml
        """

    async def aclose(self) -> None:
        """Release network resources (HTTP connections). Override if needed."""

    # Allows: `async with client: ...`, which closes connections automatically.
    async def __aenter__(self) -> "LLMClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()
