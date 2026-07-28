"""Poll trainer decisions and execute approved Telegram behavior plans."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import UTC, datetime

from conversation_agent.storage.conversation_models import ApprovalAction
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository
from conversation_agent.telegram.behavior import TelegramBehaviorRuntime

logger = logging.getLogger(__name__)


class ApprovalActionWorker:
    def __init__(
        self,
        *,
        repository: SQLiteFeedbackRepository,
        behavior_runtime: TelegramBehaviorRuntime,
        poll_interval_seconds: float,
    ) -> None:
        self.repository = repository
        self.behavior_runtime = behavior_runtime
        self.poll_interval_seconds = poll_interval_seconds
        self._contact_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

    async def run(self, stop: asyncio.Event) -> None:
        tasks: set[asyncio.Task[None]] = set()
        while not stop.is_set():
            action = self.repository.claim_next_trainer_action(
                claimed_at=datetime.now(UTC).isoformat()
            )
            if action is None:
                try:
                    await asyncio.wait_for(stop.wait(), timeout=self.poll_interval_seconds)
                except TimeoutError:
                    continue
                continue
            task = asyncio.create_task(self._process(action))
            tasks.add(task)
            task.add_done_callback(tasks.discard)
        if tasks:
            await asyncio.gather(*tasks)

    async def _process(self, action: ApprovalAction) -> None:
        draft = self.repository.get_agent_draft(action.draft_id)
        contact_id = draft.contact_id if draft is not None else f"missing:{action.draft_id}"
        async with self._contact_locks[contact_id]:
            try:
                await self._handle(action.draft_id, action.action, action.payload_text)
            except Exception as exc:  # noqa: BLE001
                logger.error(
                    "Trainer action failed action_id=%s draft_id=%s error_type=%s",
                    action.id,
                    action.draft_id,
                    type(exc).__name__,
                )
                self.repository.finish_trainer_action(
                    action.id,
                    status="failed",
                    completed_at=datetime.now(UTC).isoformat(),
                    error_category=type(exc).__name__,
                )
                failed_at = datetime.now(UTC).isoformat()
                self.repository.update_draft_status(
                    action.draft_id,
                    "failed",
                    updated_at=failed_at,
                )
                self.repository.mark_delivery(action.draft_id, status="failed")
            else:
                self.repository.finish_trainer_action(
                    action.id,
                    status="completed",
                    completed_at=datetime.now(UTC).isoformat(),
                )

    async def _handle(
        self,
        draft_id: int,
        action: str,
        payload_text: str | None,
    ) -> None:
        draft = self.repository.get_agent_draft(draft_id)
        if draft is None:
            return
        now = datetime.now(UTC).isoformat()
        if action in {"approve", "fix"}:
            if draft.status == "stale":
                return
            self.repository.record_runtime_event(
                event_type="trainer_approved" if action == "approve" else "trainer_fixed",
                occurred_at=now,
                conversation_id=draft.conversation_id,
                message_group_id=draft.message_group_id,
                draft_id=draft_id,
                behavior_plan_id=draft.behavior_plan_id,
            )
            self.repository.update_draft_status(
                draft_id,
                "approved",
                updated_at=now,
                approved_by="trainer_bot",
            )
            await self.behavior_runtime.execute(
                draft_id=draft_id,
                corrected_text=payload_text if action == "fix" else None,
            )
            return
        if action == "handoff":
            self.repository.start_handoff(
                conversation_id=draft.conversation_id,
                contact_id=draft.contact_id,
                reason=payload_text or "trainer requested handoff",
                created_at=now,
                draft_id=draft_id,
            )
            self.repository.update_draft_status(draft_id, "handed_off", updated_at=now)
            self.repository.mark_delivery(draft_id, status="handed_off")
            self.repository.record_runtime_event(
                event_type="handoff_started",
                occurred_at=now,
                conversation_id=draft.conversation_id,
                message_group_id=draft.message_group_id,
                draft_id=draft_id,
                behavior_plan_id=draft.behavior_plan_id,
            )
            return
        if action in {"reject", "skip"}:
            status = "rejected" if action == "reject" else "skipped"
            self.repository.update_draft_status(draft_id, status, updated_at=now)
            self.repository.mark_delivery(draft_id, status=status)
            self.repository.record_runtime_event(
                event_type="trainer_rejected",
                occurred_at=now,
                conversation_id=draft.conversation_id,
                message_group_id=draft.message_group_id,
                draft_id=draft_id,
                behavior_plan_id=draft.behavior_plan_id,
                metadata_json=f'{{"action":"{action}"}}',
            )
