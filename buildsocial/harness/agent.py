"""Generic agent: a system prompt + typed input/output + a validate-and-retry loop.

Turns an LLM's free text into a validated Pydantic object:

    messages = [system prompt, user message built from the input]
    repeat up to 1 + max_retries times:
        reply  = llm.complete(messages, output_schema=OutputT)
        errors = extract JSON -> validate with Pydantic -> agent's check() rules
        no errors -> return AgentResult
        errors    -> append the reply + "fix these errors" and try again
    still failing -> raise AgentError

Knows nothing about social media; concrete agents live in buildsocial/agents/.
Tool use comes later (Step 4).
"""

import re
from abc import ABC, abstractmethod
from typing import Generic, TypeVar

from pydantic import BaseModel, ValidationError

from buildsocial.harness.llm.base import LLMClient
from buildsocial.harness.llm.types import LLMResponse, Message, Usage

InputT = TypeVar("InputT", bound=BaseModel)
OutputT = TypeVar("OutputT", bound=BaseModel)


class AgentResult(BaseModel, Generic[OutputT]):
    output: OutputT
    usage: Usage  # summed over ALL attempts: the real cost of getting this output
    attempts: int
    model: str


class AgentError(Exception):
    """The agent couldn't produce valid output within its retry budget."""

    def __init__(self, agent: str, errors_by_attempt: list[list[str]], usage: Usage):
        self.agent = agent
        self.errors_by_attempt = errors_by_attempt
        self.usage = usage
        lines = [f"agent '{agent}' failed after {len(errors_by_attempt)} attempts:"]
        for i, errors in enumerate(errors_by_attempt, 1):
            lines += [f"  attempt {i}:"] + [f"    - {e}" for e in errors]
        super().__init__("\n".join(lines))


class Agent(ABC, Generic[InputT, OutputT]):
    """Base class for agents. Subclasses set the class attributes and build_user_message()."""

    name: str  # also the key under `agents:` in models.yaml
    output_type: type[OutputT]
    system_prompt: str
    max_retries: int = 2  # extra attempts after the first one

    @abstractmethod
    def build_user_message(self, input: InputT) -> str:
        """Turn the typed input into the user message the LLM sees."""

    def check(self, output: OutputT, input: InputT) -> list[str]:
        """Deterministic rules the LLM can't be trusted with (lengths, counts, ...).

        Return a list of human-readable problems; empty list = all good.
        Override in subclasses.
        """
        return []

    async def run(self, input: InputT, llm: LLMClient) -> AgentResult[OutputT]:
        # The LLM client is passed in (dependency injection): the CLI passes a real
        # Sarvam client, tests pass a fake one. The agent never builds its own.
        messages = [Message.system(self.system_prompt), Message.user(self.build_user_message(input))]
        usage = Usage()
        errors_by_attempt: list[list[str]] = []

        for attempt in range(1, self.max_retries + 2):
            resp = await llm.complete(messages, output_schema=self.output_type)
            usage = usage + resp.usage

            output, errors = self._parse(resp, input)
            if not errors:
                assert output is not None
                return AgentResult[self.output_type](
                    output=output, usage=usage, attempts=attempt, model=resp.model
                )

            errors_by_attempt.append(errors)
            # Show the model its own answer and what's wrong with it, then ask again.
            messages += [resp.to_message(), Message.user(_fix_request(errors))]

        raise AgentError(self.name, errors_by_attempt, usage)

    def _parse(self, resp: LLMResponse, input: InputT) -> tuple[OutputT | None, list[str]]:
        """Response -> (output, errors). Exactly one of them is meaningful."""
        if resp.stop_reason == "max_tokens":
            return None, ["Your response was cut off (output token limit). Be more concise."]

        raw = extract_json(resp.text)
        if raw is None:
            return None, ["Your response did not contain a JSON object."]

        try:
            output = self.output_type.model_validate_json(raw)
        except ValidationError as e:
            return None, [_format_validation_error(err) for err in e.errors()]

        return output, self.check(output, input)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------

_FENCED_JSON = re.compile(r"```(?:json)?\s*(\{.*\})\s*```", re.DOTALL)


def extract_json(text: str | None) -> str | None:
    """Pull the JSON object out of a model reply.

    Models sometimes wrap JSON in ```json fences or add a sentence around it,
    even when asked not to. Take the fenced block if there is one, otherwise
    everything from the first '{' to the last '}'.
    """
    if not text:
        return None
    fenced = _FENCED_JSON.search(text)
    if fenced:
        return fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return None
    return text[start : end + 1]


def _format_validation_error(err: dict) -> str:
    # e.g. {"loc": ("hashtags", 0), "msg": "Input should be a valid string"}
    #   -> "hashtags.0: Input should be a valid string"
    location = ".".join(str(part) for part in err["loc"]) or "(whole object)"
    return f"{location}: {err['msg']}"


def _fix_request(errors: list[str]) -> str:
    bullet_list = "\n".join(f"- {e}" for e in errors)
    return (
        "Your previous response had these problems:\n"
        f"{bullet_list}\n\n"
        "Return the complete corrected JSON object only, with no other text."
    )
