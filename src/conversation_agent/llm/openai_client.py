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
        try:
            response: Any = await self._client.responses.create(
                model=self._model,
                instructions=instructions,
                input=input_messages,
                store=False,
            )
            return str(getattr(response, "output_text", "") or "").strip()
        except Exception:  # noqa: BLE001
            formatted_messages: Any = [{"role": "system", "content": instructions}] + list(messages)
            chat_response: Any = await self._client.chat.completions.create(
                model=self._model,
                messages=formatted_messages,
            )
            return str(chat_response.choices[0].message.content or "").strip()
