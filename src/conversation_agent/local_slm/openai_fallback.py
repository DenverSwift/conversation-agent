"""OpenAI fallback adapter for hybrid local generation experiments."""

from __future__ import annotations

import time

from conversation_agent.llm.openai_client import OpenAIReplyClient
from conversation_agent.local_slm.models import GenerationRequest, GenerationResult


class OpenAIReplyFallbackProvider:
    provider_name = "openai-fallback"

    def __init__(self, *, api_key: str, model: str, timeout_seconds: float) -> None:
        self._client = OpenAIReplyClient(
            api_key=api_key,
            model=model,
            timeout_seconds=timeout_seconds,
        )

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        started = time.perf_counter()
        text = await self._client.create_reply(
            instructions=(
                "Generate short Telegram messages. Return natural text only, "
                "one message per line when multiple bubbles are useful."
            ),
            messages=[{"role": "user", "content": request.context.render(budget_chars=4000)}],
        )
        messages = tuple(line.strip() for line in text.splitlines() if line.strip())
        if not messages and text.strip():
            messages = (text.strip(),)
        return GenerationResult(
            action="reply" if messages else "handoff",
            messages=messages,
            handoff_required=not messages,
            confidence=0.65 if messages else 0.0,
            provider=self.provider_name,
            raw_output=text,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
