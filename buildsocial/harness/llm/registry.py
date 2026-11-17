"""Builds LLM clients from models.yaml + .env.

Usage:
    llm = get_client("research")      # agent name -> that agent's model
    llm = get_client("sarvam-105b")   # or a model alias directly
    llm = get_client()                # the default model

This is the ONLY place that decides which provider/model a piece of code gets.
Agents receive an LLMClient and never build one themselves.
"""

import os
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, model_validator

from buildsocial.harness.llm.base import LLMClient
from buildsocial.harness.llm.gemini import GeminiClient
from buildsocial.harness.llm.sarvam import SarvamClient
from buildsocial.harness.llm.types import Usage

# ----------------------------------------------------------------------
# Config schema: what a valid models.yaml looks like
# ----------------------------------------------------------------------


class TokenPrices(BaseModel):
    """Price per 1 million tokens. None = unknown."""

    input: float | None = None
    cache_read: float | None = None
    cache_write: float | None = None
    output: float | None = None


class Pricing(BaseModel):
    currency: str = "USD"
    per_1m_tokens: TokenPrices = TokenPrices()
    per_1k_web_searches: float | None = None  # built-in search tools bill per query

    def cost(self, usage: Usage) -> float:
        """Cost of the given usage in `currency`. Unknown prices count as 0."""
        p = self.per_1m_tokens
        buckets = [
            (usage.input_tokens, p.input),
            (usage.cache_read_tokens, p.cache_read),
            (usage.cache_write_tokens, p.cache_write),
            (usage.output_tokens, p.output),  # reasoning is already inside output_tokens
        ]
        tokens_cost = sum(tokens * (price or 0) for tokens, price in buckets) / 1_000_000
        search_cost = usage.web_searches * (self.per_1k_web_searches or 0) / 1_000
        return tokens_cost + search_cost


class ModelConfig(BaseModel):
    provider: Literal["sarvam", "gemini"]  # extend as adapters are added
    model: str
    base_url: str | None = None
    api_key_env: str
    params: dict[str, Any] = {}
    pricing: Pricing = Pricing()
    # Seconds to wait for one HTTP response before treating it as hung and retrying.
    timeout_s: float = 120.0


class ModelsFile(BaseModel):
    models: dict[str, ModelConfig]
    agents: dict[str, str]

    @model_validator(mode="after")
    def _check_references(self) -> "ModelsFile":
        if "default" not in self.agents:
            raise ValueError("agents section needs a 'default' entry")
        for agent, alias in self.agents.items():
            if alias not in self.models:
                raise ValueError(f"agent '{agent}' uses unknown model '{alias}'")
        return self


# ----------------------------------------------------------------------
# Adapters: provider name -> function that builds the client
# ----------------------------------------------------------------------


def _client_kwargs(cfg: ModelConfig, api_key: str) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "api_key": api_key, "model": cfg.model, "default_params": cfg.params, "timeout": cfg.timeout_s,
    }
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return kwargs


ADAPTERS: dict[str, Callable[[ModelConfig, str], LLMClient]] = {
    "sarvam": lambda cfg, key: SarvamClient(**_client_kwargs(cfg, key)),
    "gemini": lambda cfg, key: GeminiClient(**_client_kwargs(cfg, key)),
}

# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------


def _config_path() -> Path:
    # Override with BUILDSOCIAL_MODELS=/path/to/models.yaml (useful for tests/evals).
    return Path(os.environ.get("BUILDSOCIAL_MODELS", "models.yaml"))


@lru_cache(maxsize=1)  # read the file once per process
def load_config() -> ModelsFile:
    load_dotenv()  # puts .env values into os.environ (doesn't override real env vars)
    path = _config_path()
    if not path.exists():
        raise FileNotFoundError(f"{path} not found (run from the repo root, or set BUILDSOCIAL_MODELS)")
    with path.open() as f:
        return ModelsFile.model_validate(yaml.safe_load(f))


def resolve_model(name: str = "default") -> tuple[str, ModelConfig]:
    """Agent name or model alias -> (alias, config).

    Lookup order: agent listed under `agents:` -> model alias under `models:`
    -> otherwise an agent that isn't listed, so it gets the default model.
    """
    cfg = load_config()
    if name in cfg.agents:
        alias = cfg.agents[name]
    elif name in cfg.models:
        alias = name
    else:
        alias = cfg.agents["default"]
    return alias, cfg.models[alias]


def get_client(name: str = "default") -> LLMClient:
    """Build a ready-to-use client for an agent name or model alias."""
    alias, model_cfg = resolve_model(name)
    api_key = os.environ.get(model_cfg.api_key_env)
    if not api_key:
        raise RuntimeError(f"model '{alias}' needs {model_cfg.api_key_env} set in .env")
    return ADAPTERS[model_cfg.provider](model_cfg, api_key)
