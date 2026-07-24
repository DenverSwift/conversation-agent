"""OpenAI Responses API client."""

from __future__ import annotations

from typing import Any


class OpenAIReplyClient:
    def __init__(self, *, api_key: str, model: str, timeout_seconds: float) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, timeout=timeout_seconds)
        self._model = model

    async def create_reply(self, *, instructions: str, messages: list[dict[str, str]]) -> str:
        input_messages: Any = messages
        response: Any = await self._client.responses.create(
            model=self._model,
            instructions=instructions,
            input=input_messages,
            store=False,
        )
        return str(getattr(response, "output_text", "") or "").strip()
