"""High-level reply generation wrapper."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from conversation_agent.agent.context_builder import ChatMessage, messages_for_openai


class ReplyClient(Protocol):
    async def create_reply(self, *, instructions: str, messages: list[dict[str, str]]) -> str:
        """Create a reply from normalized conversation messages."""


class Responder:
    def __init__(self, client: ReplyClient, instructions: str) -> None:
        self.client = client
        self.instructions = instructions

    async def reply(self, messages: Sequence[ChatMessage]) -> str:
        return await self.client.create_reply(
            instructions=self.instructions,
            messages=messages_for_openai(list(messages)),
        )
