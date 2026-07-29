"""Runtime configuration for the stage 1 local inference path."""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_LOCAL_MODEL = "Qwen/Qwen3-0.6B-GGUF:Q8_0"
DEFAULT_LOCAL_BASE_URL = "http://127.0.0.1:8080/v1"


@dataclass(frozen=True)
class LocalLLMConfig:
    base_url: str = DEFAULT_LOCAL_BASE_URL
    model: str = DEFAULT_LOCAL_MODEL
    api_key: str = "local-no-key"
    timeout_seconds: float = 30.0
    max_output_tokens: int = 256
    context_tokens: int = 4096
    temperature: float = 0.6
    top_k: int = 20
    top_p: float = 0.95
    min_p: float = 0.0
    presence_penalty: float = 1.5
    thinking: bool = False
    seed: int | None = None

    @classmethod
    def from_env(cls) -> LocalLLMConfig:
        return cls(
            base_url=_env("LOCAL_LLM_BASE_URL", _env("LOCAL_GENERATION_BASE_URL", DEFAULT_LOCAL_BASE_URL)),
            model=_env("LOCAL_LLM_MODEL", _env("LOCAL_GENERATION_MODEL", DEFAULT_LOCAL_MODEL)),
            api_key=_env("LOCAL_LLM_API_KEY", "local-no-key"),
            timeout_seconds=_float("LOCAL_LLM_TIMEOUT_SECONDS", _float("LOCAL_GENERATION_TIMEOUT_SECONDS", 30.0)),
            max_output_tokens=_int(
                "LOCAL_LLM_MAX_OUTPUT_TOKENS",
                _int("LOCAL_GENERATION_MAX_OUTPUT_TOKENS", 256),
            ),
            context_tokens=_int("LOCAL_LLM_CONTEXT_TOKENS", 4096),
            temperature=_float("LOCAL_LLM_TEMPERATURE", _float("LOCAL_GENERATION_TEMPERATURE", 0.6)),
            top_k=_int("LOCAL_LLM_TOP_K", 20),
            top_p=_float("LOCAL_LLM_TOP_P", _float("LOCAL_GENERATION_TOP_P", 0.95)),
            min_p=_float("LOCAL_LLM_MIN_P", 0.0),
            presence_penalty=_float("LOCAL_LLM_PRESENCE_PENALTY", 1.5),
            thinking=_bool("LOCAL_LLM_THINKING", False),
            seed=_optional_int("LOCAL_LLM_SEED"),
        )


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def _int(name: str, default: int) -> int:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return int(value)


def _optional_int(name: str) -> int | None:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return None
    return int(value)


def _float(name: str, default: float) -> float:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return float(value)


def _bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}
