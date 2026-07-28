"""Per-chat asynchronous debounce for incoming Telegram message bursts."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from time import monotonic

from conversation_agent.domain.models import IncomingMessage, IncomingMessageGroup


@dataclass
class _ChatBuffer:
    messages: list[IncomingMessage] = field(default_factory=list)
    started_at: float = field(default_factory=monotonic)
    changed: asyncio.Event = field(default_factory=asyncio.Event)
    task: asyncio.Task[None] | None = None


class IncomingMessageBuffer:
    def __init__(
        self,
        *,
        minimum_wait_seconds: float,
        maximum_wait_seconds: float,
        on_group: Callable[[IncomingMessageGroup], Awaitable[None]],
        urgent_bypass: Callable[[IncomingMessage], bool] | None = None,
    ) -> None:
        if minimum_wait_seconds <= 0 or maximum_wait_seconds < minimum_wait_seconds:
            raise ValueError("Invalid accumulation window")
        self.minimum_wait_seconds = minimum_wait_seconds
        self.maximum_wait_seconds = maximum_wait_seconds
        self.on_group = on_group
        self.urgent_bypass = urgent_bypass or (lambda message: False)
        self._buffers: dict[str, _ChatBuffer] = {}
        self._lock = asyncio.Lock()

    async def add(self, message: IncomingMessage) -> None:
        async with self._lock:
            state = self._buffers.get(message.contact_id)
            if state is None:
                state = _ChatBuffer()
                self._buffers[message.contact_id] = state
                state.messages.append(message)
                state.task = asyncio.create_task(self._run(message.contact_id, state))
            else:
                state.messages.append(message)
                state.changed.set()
            if self.urgent_bypass(message):
                state.changed.set()
                if state.task is not None:
                    state.task.cancel()
                state.task = asyncio.create_task(self.flush(message.contact_id))

    async def flush(self, contact_id: str) -> None:
        async with self._lock:
            state = self._buffers.pop(contact_id, None)
        if state is None or not state.messages:
            return
        messages = tuple(sorted(state.messages, key=lambda item: item.message_id))
        group = IncomingMessageGroup(
            group_id=f"{contact_id}:{messages[0].message_id}-{messages[-1].message_id}",
            contact_id=contact_id,
            messages=messages,
            started_at=messages[0].received_at,
            completed_at=messages[-1].received_at,
        )
        await self.on_group(group)

    async def close(self) -> None:
        async with self._lock:
            contact_ids = list(self._buffers)
            tasks = [
                state.task
                for state in self._buffers.values()
                if state.task is not None and not state.task.done()
            ]
        for task in tasks:
            task.cancel()
        for contact_id in contact_ids:
            await self.flush(contact_id)

    async def _run(self, contact_id: str, state: _ChatBuffer) -> None:
        try:
            while True:
                elapsed = monotonic() - state.started_at
                remaining = self.maximum_wait_seconds - elapsed
                if remaining <= 0:
                    break
                state.changed.clear()
                try:
                    await asyncio.wait_for(
                        state.changed.wait(),
                        timeout=min(self.minimum_wait_seconds, remaining),
                    )
                except TimeoutError:
                    break
            await self.flush(contact_id)
        except asyncio.CancelledError:
            return
