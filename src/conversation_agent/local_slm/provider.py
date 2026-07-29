"""Local generation providers without mandatory heavyweight dependencies."""

from __future__ import annotations

import asyncio
import json
import time
import urllib.error
import urllib.request
from typing import Any, Protocol, cast

from conversation_agent.local_slm.models import Action, GenerationRequest, GenerationResult


class GenerationProvider(Protocol):
    provider_name: str

    async def health_check(self) -> bool:
        """Return whether the provider is available."""
        ...

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate Telegram bubbles for a compact local context."""
        ...


class FakeLocalGenerationProvider:
    provider_name = "fake-local"

    async def health_check(self) -> bool:
        return True

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        if request.policy.action != "reply":
            return GenerationResult(
                action=request.policy.action,
                messages=(),
                confidence=request.policy.confidence,
                handoff_required=request.policy.needs_handoff,
                provider=self.provider_name,
            )
        text = _latest_contact_text(request)
        if "бот" in text.lower() or "автомат" in text.lower():
            messages = ("да, можем", "что именно надо автоматизировать?")
        else:
            messages = ("понял", "расскажи чуть подробнее")
        return GenerationResult(
            action="reply",
            messages=messages[: request.policy.suggested_bubble_count or 1],
            confidence=0.82,
            provider=self.provider_name,
            raw_output=json.dumps({"messages": messages}, ensure_ascii=False),
            latency_ms=1,
        )


class OpenAICompatibleLocalProvider:
    """Client for llama.cpp/vLLM/OpenAI-compatible local HTTP endpoints."""

    provider_name = "local-openai-compatible"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout_seconds: float = 20,
        max_output_tokens: int = 256,
        temperature: float = 0.7,
        top_p: float = 0.9,
        seed: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.seed = seed

    async def health_check(self) -> bool:
        return await asyncio.to_thread(self._health_check_sync)

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Return strict JSON with action, messages, reaction, "
                        "handoff_required, confidence. Keep Telegram messages short."
                    ),
                },
                {
                    "role": "user",
                    "content": request.context.render(budget_chars=4000),
                },
            ],
            "temperature": self.temperature,
            "top_p": self.top_p,
            "max_tokens": self.max_output_tokens,
        }
        if self.seed is not None:
            payload["seed"] = self.seed
        raw = await asyncio.to_thread(self._post_json, "/chat/completions", payload)
        text = _extract_text(raw)
        result = _parse_generation_text(text, provider=self.provider_name)
        return GenerationResult(
            action=result.action,
            messages=result.messages,
            reaction=result.reaction,
            handoff_required=result.handoff_required,
            confidence=result.confidence,
            provider=self.provider_name,
            raw_output=text,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    def _health_check_sync(self) -> bool:
        try:
            self._get_json("/models")
        except Exception:  # noqa: BLE001
            return False
        return True

    def _get_json(self, path: str) -> Any:
        with urllib.request.urlopen(
            self.base_url + path,
            timeout=self.timeout_seconds,
        ) as response:
            return json.loads(response.read().decode("utf-8"))

    def _post_json(self, path: str, payload: dict[str, Any]) -> Any:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(
            self.base_url + path,
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise TimeoutError(f"Local generation endpoint unavailable: {exc}") from exc


def _extract_text(raw: Any) -> str:
    try:
        return str(raw["choices"][0]["message"]["content"])
    except Exception as exc:
        raise ValueError("OpenAI-compatible endpoint returned an unsupported response") from exc


def _parse_generation_text(text: str, *, provider: str) -> GenerationResult:
    parsed = json.loads(text)
    messages = tuple(str(item).strip() for item in parsed.get("messages", []) if str(item).strip())
    return GenerationResult(
        action=cast(Action, parsed.get("action", "reply")),
        messages=messages,
        reaction=parsed.get("reaction"),
        handoff_required=bool(parsed.get("handoff_required", False)),
        confidence=float(parsed.get("confidence", 0.0)),
        provider=provider,
        raw_output=text,
    )


def _latest_contact_text(request: GenerationRequest) -> str:
    for turn in reversed(request.context.conversation):
        if turn.get("role") in {"user", "contact"}:
            return turn.get("content", "")
    return ""
