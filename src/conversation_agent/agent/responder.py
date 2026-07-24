"""High-level reply generation wrapper."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from conversation_agent.agent.context_builder import ChatMessage, messages_for_openai


class ReplyClient(Protocol):
    async def create_reply(self, *, instructions: str, messages: list[dict[str, str]]) -> str:
        """Create a reply from normalized conversation messages."""
        ...


class Responder:
    def __init__(
        self,
        client: ReplyClient,
        instructions: str,
        *,
        style_runtime: Any | None = None,
    ) -> None:
        self.client = client
        self.instructions = instructions
        self.style_runtime = style_runtime

    async def reply(self, messages: Sequence[ChatMessage]) -> str:
        if self.style_runtime is not None:
            composed = self.style_runtime.compose(messages)
            return await self.client.create_reply(
                instructions=composed.instructions,
                messages=composed.messages,
            )
        return await self.client.create_reply(
            instructions=self.instructions,
            messages=messages_for_openai(list(messages)),
        )
