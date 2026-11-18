"""Provider-neutral types for talking to LLMs.

Every adapter (Sarvam, OpenAI, Anthropic, ...) translates between these types
and its own API format. Agents and the harness only ever use these, so switching
providers never changes agent code.
"""

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

Role = Literal["system", "user", "assistant", "tool"]

# Why the LLM stopped generating:
#   end        - finished its answer
#   tool_calls - wants us to run tools and send back results
#   max_tokens - hit the output length limit (answer is probably cut off)
#   other      - anything provider-specific (content filter, etc.)
StopReason = Literal["end", "tool_calls", "max_tokens", "other"]

# Tools the provider runs on its own side (we don't execute them). An adapter that
# doesn't offer one raises a clear error instead of silently ignoring it.
#   web_search - search the web and ground the answer in results (Gemini: Google Search)
BuiltinTool = Literal["web_search"]


class Citation(BaseModel):
    """A source backing part of the response text (from a built-in search tool)."""

    url: str
    title: str | None = None
    cited_text: str | None = None  # the part of the response this source supports


class ToolCall(BaseModel):
    """The LLM asking us to run a tool."""

    id: str  # links this request to the result we send back
    name: str
    arguments: dict[str, Any]
    # Set when the model produced arguments that aren't valid JSON. The agent loop
    # reports this back to the model so it can retry, instead of crashing.
    arguments_error: str | None = None


class Message(BaseModel):
    """One entry in a conversation history."""

    role: Role
    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)  # only on assistant messages
    tool_call_id: str | None = None  # only on tool messages

    @model_validator(mode="after")
    def _check_role_fields(self) -> "Message":
        if self.role == "tool" and not self.tool_call_id:
            raise ValueError("tool messages need a tool_call_id")
        if self.tool_calls and self.role != "assistant":
            raise ValueError("only assistant messages can contain tool_calls")
        return self

    # Shortcuts so agent code reads naturally: Message.user("hi")
    @classmethod
    def system(cls, content: str) -> "Message":
        return cls(role="system", content=content)

    @classmethod
    def user(cls, content: str) -> "Message":
        return cls(role="user", content=content)

    @classmethod
    def tool_result(cls, tool_call_id: str, content: str) -> "Message":
        return cls(role="tool", tool_call_id=tool_call_id, content=content)


class ToolSpec(BaseModel):
    """A tool the LLM is allowed to call."""

    name: str
    description: str  # the LLM reads this to decide when to use the tool
    parameters: dict[str, Any]  # JSON Schema describing the arguments


class Usage(BaseModel):
    """Token counts for one or more LLM calls.

    The three input buckets do NOT overlap, because each is billed at its own price.
    Adapters must convert to this convention (e.g. OpenAI-format prompt_tokens
    INCLUDES cached tokens, so the adapter subtracts them out).
    """

    input_tokens: int = 0        # uncached input, full price
    cache_read_tokens: int = 0   # input served from the provider's cache (discounted)
    cache_write_tokens: int = 0  # input written into the cache (some providers charge extra)
    output_tokens: int = 0       # everything generated, INCLUDING reasoning tokens
    reasoning_tokens: int = 0    # hidden "thinking" tokens; a subset of output_tokens, for visibility
    web_searches: int = 0        # search queries run by a built-in search tool (billed per query)

    @property
    def total_input_tokens(self) -> int:
        return self.input_tokens + self.cache_read_tokens + self.cache_write_tokens

    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        # Lets us sum usage across a whole run: total = usage1 + usage2 + ...
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            cache_read_tokens=self.cache_read_tokens + other.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            web_searches=self.web_searches + other.web_searches,
        )


class LLMResponse(BaseModel):
    """What every adapter returns, regardless of provider."""

    text: str | None = None
    reasoning: str | None = None  # model's visible "thinking" text, if the provider returns it (for traces)
    tool_calls: list[ToolCall] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)  # sources, when a built-in search tool was used
    search_queries: list[str] = Field(default_factory=list)  # what the built-in search actually searched for
    http_retries: list[str] = Field(default_factory=list)  # transient failures retried before this reply
    stop_reason: StopReason
    usage: Usage  # converted to our convention by the adapter
    raw_usage: dict[str, Any] = Field(default_factory=dict)  # provider's original usage JSON, kept for auditing
    model: str  # the exact model that answered (useful in traces)

    def to_message(self) -> Message:
        """Turn this response into an assistant message to append to the history."""
        return Message(role="assistant", content=self.text, tool_calls=self.tool_calls)
