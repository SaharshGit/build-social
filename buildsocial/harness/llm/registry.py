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
from buildsocial.harness.llm.sarvam import SarvamClient

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


class ModelConfig(BaseModel):
    provider: Literal["sarvam"]  # extend as adapters are added
    model: str
    base_url: str | None = None
    api_key_env: str
    params: dict[str, Any] = {}
    pricing: Pricing = Pricing()


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


def _build_sarvam(cfg: ModelConfig, api_key: str) -> LLMClient:
    kwargs: dict[str, Any] = {"api_key": api_key, "model": cfg.model, "default_params": cfg.params}
    if cfg.base_url:
        kwargs["base_url"] = cfg.base_url
    return SarvamClient(**kwargs)


ADAPTERS: dict[str, Callable[[ModelConfig, str], LLMClient]] = {
    "sarvam": _build_sarvam,
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
    """Agent name or model alias -> (alias, config)."""
    cfg = load_config()
    alias = cfg.agents.get(name, name)  # agent name? map to its alias. Otherwise treat as alias.
    if alias not in cfg.models:
        raise KeyError(
            f"'{name}' is neither an agent nor a model alias. "
            f"Agents: {sorted(cfg.agents)}. Models: {sorted(cfg.models)}"
        )
    return alias, cfg.models[alias]


def get_client(name: str = "default") -> LLMClient:
    """Build a ready-to-use client for an agent name or model alias."""
    alias, model_cfg = resolve_model(name)
    api_key = os.environ.get(model_cfg.api_key_env)
    if not api_key:
        raise RuntimeError(f"model '{alias}' needs {model_cfg.api_key_env} set in .env")
    return ADAPTERS[model_cfg.provider](model_cfg, api_key)
