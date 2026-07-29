"""Hybrid routing between local generation, OpenAI fallback, and handoff."""

from __future__ import annotations

import asyncio
from typing import Protocol

from conversation_agent.local_slm.models import (
    GenerationMode,
    GenerationRequest,
    GenerationResult,
    HybridResult,
)
from conversation_agent.local_slm.provider import GenerationProvider
from conversation_agent.local_slm.validator import OutputValidator


class OpenAIFallbackProvider(Protocol):
    provider_name: str

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        """Generate using a non-local fallback."""
        ...


class HybridGenerationRouter:
    def __init__(
        self,
        *,
        local_provider: GenerationProvider,
        validator: OutputValidator,
        mode: GenerationMode = "local_only",
        fallback_provider: OpenAIFallbackProvider | None = None,
        low_confidence_threshold: float = 0.55,
    ) -> None:
        self.local_provider = local_provider
        self.fallback_provider = fallback_provider
        self.validator = validator
        self.mode = mode
        self.low_confidence_threshold = low_confidence_threshold

    async def generate(self, request: GenerationRequest) -> HybridResult:
        if request.policy.action != "reply":
            selected = GenerationResult(
                action=request.policy.action,
                messages=(),
                handoff_required=request.policy.needs_handoff,
                confidence=request.policy.confidence,
                provider="policy",
            )
            validation = self.validator.validate(selected)
            return HybridResult(selected, validation, False, {"policy": selected}, ("policy",))

        if self.mode == "openai_only":
            fallback = await self._fallback_or_handoff(request, reason="openai_only")
            validation = self.validator.validate(fallback)
            return HybridResult(fallback, validation, True, {"fallback": fallback}, ("openai",))

        if self.mode == "compare_shadow" and self.fallback_provider is not None:
            local_result, fallback_result = await asyncio.gather(
                self._safe_local(request),
                self._safe_fallback(request),
            )
            validation = self.validator.validate(local_result)
            selected = local_result if validation.valid else fallback_result
            return HybridResult(
                selected,
                validation,
                selected.provider == fallback_result.provider,
                {"candidate_a": local_result, "candidate_b": fallback_result},
                ("local", "openai_shadow"),
            )

        local_result = await self._safe_local(request)
        validation = self.validator.validate(local_result)
        should_fallback = (
            self.mode == "local_with_fallback"
            and self.fallback_provider is not None
            and (not validation.valid or local_result.confidence < self.low_confidence_threshold)
        )
        if should_fallback:
            fallback = await self._safe_fallback(request)
            fallback_validation = self.validator.validate(fallback)
            if fallback_validation.valid:
                return HybridResult(
                    fallback,
                    fallback_validation,
                    True,
                    {"local": local_result, "fallback": fallback},
                    ("local", "openai_fallback"),
                )
        if not validation.valid and self.mode == "local_only":
            handoff = _handoff("local_invalid")
            return HybridResult(
                handoff,
                self.validator.validate(handoff),
                False,
                {"local": local_result},
                ("local", "handoff"),
            )
        return HybridResult(local_result, validation, False, {"local": local_result}, ("local",))

    async def _safe_local(self, request: GenerationRequest) -> GenerationResult:
        try:
            return await self.local_provider.generate(request)
        except Exception as exc:  # noqa: BLE001
            return _handoff(f"local_error:{type(exc).__name__}")

    async def _safe_fallback(self, request: GenerationRequest) -> GenerationResult:
        return await self._fallback_or_handoff(request, reason="fallback")

    async def _fallback_or_handoff(self, request: GenerationRequest, *, reason: str) -> GenerationResult:
        if self.fallback_provider is None:
            return _handoff(reason)
        try:
            return await self.fallback_provider.generate(request)
        except Exception as exc:  # noqa: BLE001
            return _handoff(f"fallback_error:{type(exc).__name__}")


def _handoff(reason: str) -> GenerationResult:
    return GenerationResult(
        action="handoff",
        messages=(),
        handoff_required=True,
        confidence=0.0,
        provider=reason,
    )

