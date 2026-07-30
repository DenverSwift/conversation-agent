"""OpenAI Responses API client."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class StructuredReply:
    text: str
    model: str
    response_id: str | None
    prompt_tokens: int | None
    completion_tokens: int | None
    total_tokens: int | None


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

    async def create_structured_reply(
        self,
        *,
        instructions: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any],
        max_output_tokens: int,
        temperature: float,
        top_p: float,
    ) -> StructuredReply:
        """Create strict JSON through Responses API without storing the response."""
        input_messages: Any = messages
        response: Any = await self._client.responses.create(
            model=self._model,
            instructions=instructions,
            input=input_messages,
            text={
                "format": {
                    "type": "json_schema",
                    "name": "telegram_response",
                    "schema": schema,
                    "strict": True,
                }
            },
            max_output_tokens=max_output_tokens,
            temperature=temperature,
            top_p=top_p,
            store=False,
        )
        usage = getattr(response, "usage", None)
        prompt_tokens = getattr(usage, "input_tokens", None)
        completion_tokens = getattr(usage, "output_tokens", None)
        total_tokens = getattr(usage, "total_tokens", None)
        return StructuredReply(
            text=str(getattr(response, "output_text", "") or "").strip(),
            model=str(getattr(response, "model", self._model) or self._model),
            response_id=(
                str(getattr(response, "id", "") or "").strip()
                or None
            ),
            prompt_tokens=prompt_tokens if isinstance(prompt_tokens, int) else None,
            completion_tokens=(
                completion_tokens if isinstance(completion_tokens, int) else None
            ),
            total_tokens=total_tokens if isinstance(total_tokens, int) else None,
        )
