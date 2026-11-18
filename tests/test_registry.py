"""Tests for model resolution from models.yaml (no API calls)."""

import pytest

from buildsocial.harness.llm import registry

MODELS_YAML = """
models:
  cheap:  {provider: sarvam, model: small-model, api_key_env: TEST_KEY}
  strong: {provider: sarvam, model: big-model,   api_key_env: TEST_KEY}
agents:
  default: cheap
  content: strong
"""


@pytest.fixture(autouse=True)
def models_file(tmp_path, monkeypatch):
    path = tmp_path / "models.yaml"
    path.write_text(MODELS_YAML)
    monkeypatch.setenv("BUILDSOCIAL_MODELS", str(path))
    registry.load_config.cache_clear()  # config is cached per process; reload for each test
    yield
    registry.load_config.cache_clear()


def test_listed_agent_gets_its_model():
    assert registry.resolve_model("content")[0] == "strong"


def test_model_alias_resolves_directly():
    assert registry.resolve_model("strong")[0] == "strong"


def test_unlisted_agent_falls_back_to_default():
    assert registry.resolve_model("research")[0] == "cheap"


def test_missing_api_key_is_a_clear_error(monkeypatch):
    monkeypatch.delenv("TEST_KEY", raising=False)
    with pytest.raises(RuntimeError, match="TEST_KEY"):
        registry.get_client("content")


def test_agent_pointing_at_unknown_model_fails_on_load(tmp_path, monkeypatch):
    bad = tmp_path / "bad.yaml"
    bad.write_text(MODELS_YAML.replace("content: strong", "content: typo-model"))
    monkeypatch.setenv("BUILDSOCIAL_MODELS", str(bad))
    registry.load_config.cache_clear()
    with pytest.raises(ValueError, match="unknown model 'typo-model'"):
        registry.load_config()
