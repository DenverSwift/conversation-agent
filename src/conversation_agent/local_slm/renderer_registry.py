"""Pinned local renderer model profiles."""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class RendererProfile:
    name: str
    repository: str
    revision: str
    filename: str
    quantization: str
    model_alias: str
    source_model: str | None = None
    source_revision: str | None = None
    base_url: str = "http://127.0.0.1:8080/v1"
    context_tokens: int = 4096
    max_output_tokens: int = 192
    temperature: float = 0.2
    top_p: float = 0.9
    repetition_penalty: float = 1.05

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


GGUF_REVISION = "da30124570330edcb7fe487c5b1f1ba0b0c09721"
SOURCE_REVISION = "03bcd55e56b02175bcc863c4761613b1bda8302b"
GGUF_REPOSITORY = "RefalMachine/RuadaptQwen3-4B-Instruct-GGUF"
SOURCE_REPOSITORY = "RefalMachine/RuadaptQwen3-4B-Instruct"

LOCAL_RENDERERS: dict[str, RendererProfile] = {
    "qwen3_06b_baseline": RendererProfile(
        name="qwen3_06b_baseline",
        repository="Qwen/Qwen3-0.6B-GGUF",
        revision="stage25-recorded-hf-resolution",
        filename="Q8_0.gguf",
        quantization="Q8_0",
        model_alias="Qwen/Qwen3-0.6B-GGUF:Q8_0",
        max_output_tokens=256,
        temperature=0.6,
    ),
    "ruadapt_qwen3_4b_q6": RendererProfile(
        name="ruadapt_qwen3_4b_q6",
        repository=GGUF_REPOSITORY,
        revision=GGUF_REVISION,
        filename="Q6_K.gguf",
        quantization="Q6_K",
        model_alias="ruadapt-qwen3-4b-q6",
        source_model=SOURCE_REPOSITORY,
        source_revision=SOURCE_REVISION,
    ),
    "ruadapt_qwen3_4b_q5": RendererProfile(
        name="ruadapt_qwen3_4b_q5",
        repository=GGUF_REPOSITORY,
        revision=GGUF_REVISION,
        filename="Q5_K_M.gguf",
        quantization="Q5_K_M",
        model_alias="ruadapt-qwen3-4b-q5",
        source_model=SOURCE_REPOSITORY,
        source_revision=SOURCE_REVISION,
    ),
}


def get_renderer_profile(name: str) -> RendererProfile:
    try:
        profile = LOCAL_RENDERERS[name]
    except KeyError as exc:
        raise ValueError(f"unknown local renderer profile: {name}") from exc
    prefix = f"LOCAL_RENDERER_{name.upper()}_"
    return RendererProfile(
        **{
            **profile.to_dict(),
            "base_url": os.getenv(prefix + "BASE_URL", profile.base_url),
            "context_tokens": int(
                os.getenv(prefix + "CONTEXT_TOKENS", profile.context_tokens)
            ),
            "max_output_tokens": int(
                os.getenv(prefix + "MAX_OUTPUT_TOKENS", profile.max_output_tokens)
            ),
            "temperature": float(
                os.getenv(prefix + "TEMPERATURE", profile.temperature)
            ),
            "top_p": float(os.getenv(prefix + "TOP_P", profile.top_p)),
            "repetition_penalty": float(
                os.getenv(
                    prefix + "REPETITION_PENALTY",
                    profile.repetition_penalty,
                )
            ),
        }
    )
