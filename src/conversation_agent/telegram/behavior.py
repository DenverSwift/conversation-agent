"""Telegram behavior planning and approval-triggered delivery runtime."""

from __future__ import annotations

import asyncio
import json
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from conversation_agent.domain.models import BehaviorMessage, BehaviorPlan, GeneratedResponse
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BehaviorConfig:
    typing_speed_min_chars_per_second: float
    typing_speed_max_chars_per_second: float
    delay_jitter_ms: int
    initial_read_delay_min_ms: int
    initial_read_delay_max_ms: int
    pre_typing_delay_min_ms: int
    pre_typing_delay_max_ms: int
    bubble_delay_min_ms: int
    bubble_delay_max_ms: int


class TelegramBehaviorPlanner:
    def __init__(
        self,
        config: BehaviorConfig,
        *,
        random_source: random.Random | None = None,
    ) -> None:
        self.config = config
        self.random = random_source or random.Random()

    def plan(
        self,
        response: GeneratedResponse,
        *,
        urgency: float,
        hour: int | None = None,
    ) -> BehaviorPlan:
        hour = datetime.now(UTC).hour if hour is None else hour
        night_multiplier = 1.15 if hour < 8 or hour >= 23 else 1.0
        urgency_multiplier = max(0.55, 1.0 - urgency * 0.35)
        initial = self._delay(
            self.config.initial_read_delay_min_ms,
            self.config.initial_read_delay_max_ms,
            night_multiplier * urgency_multiplier,
        )
        pre_typing = self._delay(
            self.config.pre_typing_delay_min_ms,
            self.config.pre_typing_delay_max_ms,
            night_multiplier * urgency_multiplier,
        )
        total_chars = sum(len(message) for message in response.messages)
        speed = self.random.uniform(
            self.config.typing_speed_min_chars_per_second,
            self.config.typing_speed_max_chars_per_second,
        )
        typing_duration = max(350, int((total_chars / max(speed, 0.1)) * 1000))
        messages = tuple(
            BehaviorMessage(
                text=text,
                delay_before_ms=0
                if index == 0
                else self._delay(
                    self.config.bubble_delay_min_ms,
                    self.config.bubble_delay_max_ms,
                    urgency_multiplier,
                ),
            )
            for index, text in enumerate(response.messages)
        )
        return BehaviorPlan(
            initial_read_delay_ms=initial,
            pre_typing_delay_ms=pre_typing,
            typing_duration_ms=typing_duration,
            messages=messages,
        )

    def _delay(self, minimum: int, maximum: int, multiplier: float) -> int:
        low, high = sorted((minimum, maximum))
        base = self.random.randint(low, high)
        jitter = self.random.randint(
            -self.config.delay_jitter_ms,
            self.config.delay_jitter_ms,
        )
        return max(0, int(base * multiplier) + jitter)


class TelegramBehaviorRuntime:
    def __init__(
        self,
        *,
        client: Any,
        repository: SQLiteFeedbackRepository,
        interruption_check: Callable[[str, int], bool] | None = None,
        manual_reply_check: Callable[[str, int], Awaitable[bool]] | None = None,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.client = client
        self.repository = repository
        self.interruption_check = interruption_check or (lambda contact_id, draft_id: False)
        self.manual_reply_check = manual_reply_check
        self.sleep = sleep

    async def execute(
        self,
        *,
        draft_id: int,
        corrected_text: str | None = None,
    ) -> bool:
        draft = self.repository.get_agent_draft(draft_id)
        if draft is None or draft.status not in {"pending_approval", "approved"}:
            return False
        plan_value = json.loads(draft.behavior_plan_json)
        messages = list(plan_value.get("messages", []))
        if corrected_text is not None:
            messages = [{"text": corrected_text, "delay_before_ms": 0}]
        now = datetime.now(UTC).isoformat()
        self.repository.update_draft_status(
            draft_id,
            "sending",
            updated_at=now,
            approved_by=draft.approved_by or "trainer",
        )
        self._event("behavior_started", draft)
        if await self._cancelled(draft):
            self._interrupt(draft)
            return False
        await self.sleep(max(int(plan_value.get("initial_read_delay_ms", 0)), 0) / 1000)
        mark_read = getattr(self.client, "send_read_acknowledge", None)
        if mark_read is not None:
            await mark_read(int(draft.contact_id))
        await self.sleep(max(int(plan_value.get("pre_typing_delay_ms", 0)), 0) / 1000)
        if messages:
            self._event("typing_started", draft)
            typing = getattr(self.client, "action", None)
            if typing is not None:
                async with typing(int(draft.contact_id), "typing"):
                    await self.sleep(max(int(plan_value.get("typing_duration_ms", 0)), 0) / 1000)
        sent_ids: list[int] = []
        for item in messages:
            if await self._cancelled(draft):
                self._interrupt(draft)
                return False
            await self.sleep(max(int(item.get("delay_before_ms", 0)), 0) / 1000)
            if await self._cancelled(draft):
                self._interrupt(draft)
                return False
            sent = await self.client.send_message(int(draft.contact_id), str(item["text"]))
            sent_id = int(getattr(sent, "id", 0) or 0)
            sent_ids.append(sent_id)
            sent_at = datetime.now(UTC).isoformat()
            self.repository.save_message(
                conversation_id=draft.conversation_id,
                contact_id=draft.contact_id,
                telegram_message_id=sent_id or None,
                direction="outgoing",
                provenance="human_fix" if corrected_text is not None else "ai_sent",
                text=str(item["text"]),
                created_at=sent_at,
                draft_id=draft_id,
            )
            self._event("message_sent", draft, {"telegram_message_id": sent_id})
        completed_at = datetime.now(UTC).isoformat()
        self.repository.update_draft_status(draft_id, "sent", updated_at=completed_at)
        self.repository.mark_delivery(
            draft_id,
            status="sent",
            sent_message_id=sent_ids[-1] if sent_ids else None,
            sent_at=completed_at,
        )
        return True

    async def _cancelled(self, draft: Any) -> bool:
        if self._interrupted(draft.contact_id, draft.id):
            return True
        if self.manual_reply_check is None:
            return False
        return await self.manual_reply_check(draft.contact_id, draft.incoming_message_id)

    def _interrupted(self, contact_id: str, draft_id: int) -> bool:
        draft = self.repository.get_agent_draft(draft_id)
        return bool(
            draft is None
            or draft.status == "stale"
            or self.interruption_check(contact_id, draft_id)
        )

    def _interrupt(self, draft: Any) -> None:
        now = datetime.now(UTC).isoformat()
        self.repository.update_draft_status(draft.id, "stale", updated_at=now)
        self.repository.mark_delivery(draft.id, status="stale")
        self._event("behavior_interrupted", draft)

    def _event(
        self,
        event_type: str,
        draft: Any,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.repository.record_runtime_event(
            event_type=event_type,
            occurred_at=datetime.now(UTC).isoformat(),
            conversation_id=draft.conversation_id,
            message_group_id=draft.message_group_id,
            draft_id=draft.id,
            behavior_plan_id=draft.behavior_plan_id,
            metadata_json=json.dumps(metadata or {}, separators=(",", ":")),
        )
