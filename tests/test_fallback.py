"""Fallback model tests: when it kicks in, when it must not, and config validation. No API calls."""

import pytest

from buildsocial.harness.llm import registry
from buildsocial.harness.llm.base import LLMClient, LLMError
from buildsocial.harness.llm.fallback import FallbackClient
from buildsocial.harness.llm.types import LLMResponse, Message, Usage


class Fake(LLMClient):
    def __init__(self, name, error=None):
        self.name, self.error, self.calls, self.closed = name, error, 0, False

    async def complete(self, messages, tools=None, output_schema=None, temperature=None, max_tokens=None,
                       builtin_tools=None):
        self.calls += 1
        if self.error:
            raise self.error
        return LLMResponse(text="hi", stop_reason="end", usage=Usage(), model=self.name)

    async def aclose(self):
        self.closed = True


async def test_primary_answers_when_healthy():
    primary, backup = Fake("sarvam"), Fake("deepseek")
    resp = await FallbackClient(primary, backup, "sarvam", "deepseek").complete([Message.user("x")])
    assert resp.model == "sarvam" and backup.calls == 0 and resp.http_retries == []


async def test_timeout_falls_back_and_is_recorded():
    primary = Fake("sarvam", LLMError("network error calling Sarvam: ReadTimeout", retryable=True))
    backup = Fake("deepseek")
    resp = await FallbackClient(primary, backup, "sarvam-105b", "deepseek-v4.1-flash").complete([Message.user("x")])
    assert resp.model == "deepseek"  # the trace shows who actually answered
    assert "fallback: sarvam-105b failed" in resp.http_retries[-1] and "ReadTimeout" in resp.http_retries[-1]


async def test_bad_request_does_not_fall_back():
    primary = Fake("sarvam", LLMError("400 invalid request", status_code=400, retryable=False))
    backup = Fake("deepseek")
    with pytest.raises(LLMError, match="400"):
        await FallbackClient(primary, backup, "a", "b").complete([Message.user("x")])
    assert backup.calls == 0  # our own bug must surface, not be hidden by another model


async def test_closing_closes_both():
    primary, backup = Fake("a"), Fake("b")
    async with FallbackClient(primary, backup, "a", "b"):
        pass
    assert primary.closed and backup.closed


# --- config ------------------------------------------------------------------------

BASE = """
models:
  main:   {provider: sarvam, model: m1, api_key_env: TEST_KEY, max_retries: 1, fallback: backup}
  backup: {provider: sarvam, model: m2, api_key_env: TEST_KEY}
agents:
  default: main
"""


@pytest.fixture
def models_file(tmp_path, monkeypatch):
    def write(text):
        path = tmp_path / "models.yaml"
        path.write_text(text)
        monkeypatch.setenv("BUILDSOCIAL_MODELS", str(path))
        monkeypatch.setenv("TEST_KEY", "k")
        registry.load_config.cache_clear()

    yield write
    registry.load_config.cache_clear()


def test_get_client_wraps_a_model_that_has_a_fallback(models_file):
    models_file(BASE)
    client = registry.get_client("default")
    assert isinstance(client, FallbackClient)
    assert (client.primary_name, client.fallback_name) == ("main", "backup")
    assert client.primary.max_retries == 1
    assert not isinstance(registry.get_client("backup"), FallbackClient)


@pytest.mark.parametrize("bad, error", [
    (BASE.replace("fallback: backup", "fallback: nope"), "unknown fallback 'nope'"),
    (BASE.replace("fallback: backup", "fallback: main"), "can't be itself"),
    (BASE.replace("api_key_env: TEST_KEY}\nagents", "api_key_env: TEST_KEY, fallback: main}\nagents"),
     "can't be itself or have its own fallback"),
])
def test_invalid_fallback_config_fails_on_load(models_file, bad, error):
    models_file(bad)
    with pytest.raises(ValueError, match=error):
        registry.load_config()
