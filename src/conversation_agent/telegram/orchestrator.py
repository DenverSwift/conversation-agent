"""Approval-first orchestration for real incoming Telegram messages."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from conversation_agent.agent.context_builder import build_dialog_context, telegram_text
from conversation_agent.agent.pipeline import ConversationPipeline
from conversation_agent.domain.models import (
    BusinessProfile,
    ConversationState,
    IdentityProfile,
    IncomingMessage,
    IncomingMessageGroup,
    RelationshipProfile,
    StyleProfile,
)
from conversation_agent.settings import Settings
from conversation_agent.storage.conversation_models import NewAgentDraft
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository
from conversation_agent.telegram.buffer import IncomingMessageBuffer
from conversation_agent.telegram.interruption import InterruptionController

logger = logging.getLogger(__name__)


class TelegramConversationOrchestrator:
    def __init__(
        self,
        *,
        settings: Settings,
        client: Any,
        own_user_id: int,
        repository: SQLiteFeedbackRepository,
        pipeline: ConversationPipeline,
        identity: IdentityProfile,
        business: BusinessProfile,
        style: StyleProfile,
        review_notifier: Any,
    ) -> None:
        self.settings = settings
        self.client = client
        self.own_user_id = own_user_id
        self.repository = repository
        self.pipeline = pipeline
        self.identity = identity
        self.business = business
        self.style = style
        self.review_notifier = review_notifier
        self.interruptions = InterruptionController()
        self._events: dict[tuple[str, int], Any] = {}
        self._accumulating: set[str] = set()
        self.buffer = IncomingMessageBuffer(
            minimum_wait_seconds=settings.accumulation_min_wait_seconds,
            maximum_wait_seconds=settings.accumulation_max_wait_seconds,
            on_group=self._process_group,
            urgent_bypass=(_urgent_message if settings.urgent_message_bypass else None),
        )

    async def handle_event(self, event: Any) -> None:
        contact_id = str(getattr(event, "sender_id", ""))
        if not contact_id or self.repository.handoff_active(contact_id):
            return
        message_id = int(getattr(event, "id", 0))
        text = telegram_text(event)
        received_at = _event_time(event)
        conversation_id = _conversation_id(contact_id)
        self.repository.upsert_conversation(
            conversation_id,
            contact_id,
            updated_at=received_at,
        )
        stale_ids = self.repository.mark_pending_drafts_stale(
            contact_id,
            updated_at=received_at,
        )
        for stale_id in stale_ids:
            self.repository.record_runtime_event(
                event_type="draft_stale",
                occurred_at=received_at,
                conversation_id=conversation_id,
                draft_id=stale_id,
                metadata_json='{"reason":"new_incoming_message"}',
            )
        self.interruptions.interrupt(contact_id)
        self._events[(contact_id, message_id)] = event
        self.repository.save_message(
            conversation_id=conversation_id,
            contact_id=contact_id,
            telegram_message_id=message_id,
            direction="incoming",
            provenance="contact",
            text=text,
            created_at=received_at,
        )
        self.repository.record_runtime_event(
            event_type="message_received",
            occurred_at=received_at,
            conversation_id=conversation_id,
            message_group_id=f"{contact_id}:{message_id}",
            metadata_json=json.dumps({"message_id": message_id}),
        )
        accumulation_event = (
            "accumulation_extended" if contact_id in self._accumulating else "accumulation_started"
        )
        self._accumulating.add(contact_id)
        self.repository.record_runtime_event(
            event_type=accumulation_event,
            occurred_at=received_at,
            conversation_id=conversation_id,
            message_group_id=f"{contact_id}:{message_id}",
        )
        await self.buffer.add(
            IncomingMessage(
                message_id=message_id,
                contact_id=contact_id,
                text=text,
                received_at=received_at,
            )
        )

    async def close(self) -> None:
        await self.buffer.close()

    async def _process_group(self, group: IncomingMessageGroup) -> None:
        self._accumulating.discard(group.contact_id)
        epoch = self.interruptions.current(group.contact_id)
        event = self._events.get((group.contact_id, group.last_message_id))
        if event is None:
            return
        conversation_id = _conversation_id(group.contact_id)
        self._runtime_event("analysis_started", group, conversation_id)
        peer = await _event_peer(event)
        known_ai_ids = self.repository.sent_message_ids(int(group.contact_id))
        recent = await build_dialog_context(
            self.client,
            peer,
            allowed_user_id=int(group.contact_id),
            own_user_id=self.own_user_id,
            limit=self.settings.context_message_limit,
            current_message=event,
            known_ai_message_ids=known_ai_ids,
        )
        relationship_value = self.repository.relationship_profile(group.contact_id)
        relationship = (
            RelationshipProfile(**relationship_value)
            if relationship_value is not None
            else RelationshipProfile.neutral(group.contact_id)
        )
        state_value = self.repository.conversation_state(conversation_id)
        state = (
            _state_from_dict(state_value)
            if state_value is not None
            else ConversationState.initial(group.contact_id)
        )
        try:
            result = await self.pipeline.process(
                group=group,
                recent_messages=recent,
                identity=self.identity,
                business=self.business,
                style=self.style,
                relationship=relationship,
                state=state,
            )
        except Exception as exc:  # noqa: BLE001
            self.repository.record_runtime_event(
                event_type="generation_failed",
                occurred_at=datetime.now(UTC).isoformat(),
                conversation_id=conversation_id,
                message_group_id=group.group_id,
                metadata_json=json.dumps({"error_type": type(exc).__name__}),
            )
            logger.error(
                "Draft generation failed contact_id=%s group_id=%s error_type=%s",
                group.contact_id,
                group.group_id,
                type(exc).__name__,
            )
            return
        self._runtime_event("analysis_completed", group, conversation_id)
        if self.interruptions.is_stale(group.contact_id, epoch):
            logger.info(
                "Discarded superseded generation contact_id=%s group_id=%s",
                group.contact_id,
                group.group_id,
            )
            return
        now = datetime.now(UTC).isoformat()
        self._persist_profiles(now)
        response_json = {
            **asdict(result.response),
            "incoming_message_id": group.last_message_id,
        }
        context_json = json.dumps(
            [
                {
                    "role": message.role,
                    "text": message.content,
                    "provenance": message.provenance,
                    "message_id": message.message_id,
                }
                for message in recent
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        prompt_fingerprint = hashlib.sha256(
            (
                result.prompt.instructions
                + json.dumps(result.prompt.input_messages, ensure_ascii=False)
            ).encode("utf-8")
        ).hexdigest()
        draft_id = self.repository.create_agent_draft(
            NewAgentDraft(
                conversation_id=conversation_id,
                contact_id=group.contact_id,
                message_group_id=group.group_id,
                incoming_message_id=group.last_message_id,
                incoming_message_text=group.text,
                created_at=now,
                model=self.settings.response_model or self.settings.openai_model,
                prompt_version=self.settings.prompt_version,
                generated_reply_text="\n".join(result.response.messages),
                context_json=context_json,
                analyzer_json=_json(asdict(result.analysis)),
                goal_json=_json(asdict(result.goal)),
                response_json=_json(response_json),
                behavior_plan_json=_json(result.behavior.to_dict()),
                prompt_inspection_json=_json(result.prompt.inspection),
                prompt_fingerprint=prompt_fingerprint,
                confidence=result.response.confidence,
                handoff_required=result.response.handoff_required,
                provider=self.pipeline.response_generator.provider_name,
            )
        )
        self.repository.add_retrieved_examples(
            draft_id,
            [
                (example_id, rank, score, provenance)
                for rank, (example_id, provenance, score) in enumerate(
                    zip(
                        result.prompt.retrieved_example_ids,
                        result.prompt.retrieved_example_provenance,
                        result.prompt.retrieved_example_scores,
                        strict=True,
                    ),
                    start=1,
                )
            ],
        )
        self.repository.save_relationship_profile(
            group.contact_id,
            _json(asdict(relationship)),
            confidence=relationship.confidence,
            updated_at=now,
        )
        stored_draft = self.repository.get_agent_draft(draft_id)
        next_state = ConversationState(
            contact_id=group.contact_id,
            conversation_stage=result.analysis.conversation_stage,
            detected_intent=result.analysis.intent,
            active_goal=result.goal.goal,
            known_facts=state.known_facts,
            missing_information=result.analysis.missing_information,
            objections=state.objections,
            commitments=state.commitments,
            next_recommended_action=result.goal.goal,
            human_handoff_required=result.response.handoff_required,
            confidence=result.analysis.confidence,
            pending_draft_id=draft_id,
            pending_behavior_plan_id=(
                stored_draft.behavior_plan_id if stored_draft is not None else None
            ),
        )
        self.repository.save_conversation_state(
            conversation_id,
            group.contact_id,
            _json(asdict(next_state)),
            updated_at=now,
        )
        self.repository.record_runtime_event(
            event_type="draft_created",
            occurred_at=now,
            conversation_id=conversation_id,
            message_group_id=group.group_id,
            draft_id=draft_id,
            behavior_plan_id=(
                stored_draft.behavior_plan_id if stored_draft is not None else None
            ),
            metadata_json=json.dumps(
                {
                    "should_reply": result.response.should_reply,
                    "handoff_required": result.response.handoff_required,
                    "confidence": result.response.confidence,
                },
                separators=(",", ":"),
            ),
        )
        self.repository.finish_notification(
            draft_id,
            status="pending",
            attempted_at=now,
        )
        await self.review_notifier.notify_reply(draft_id)

    def _persist_profiles(self, updated_at: str) -> None:
        self.repository.upsert_profile(
            "identities",
            self.identity.user_id,
            _json(asdict(self.identity)),
            updated_at=updated_at,
        )
        self.repository.upsert_profile(
            "business_profiles",
            "default",
            _json(asdict(self.business)),
            updated_at=updated_at,
        )
        self.repository.upsert_profile(
            "style_profiles",
            self.identity.user_id,
            _json(asdict(self.style)),
            updated_at=updated_at,
        )

    def _runtime_event(
        self,
        event_type: str,
        group: IncomingMessageGroup,
        conversation_id: str,
    ) -> None:
        self.repository.record_runtime_event(
            event_type=event_type,
            occurred_at=datetime.now(UTC).isoformat(),
            conversation_id=conversation_id,
            message_group_id=group.group_id,
        )


def should_process_event(event: Any, allowed_contact_ids: tuple[int, ...]) -> bool:
    return bool(
        not getattr(event, "out", False)
        and getattr(event, "is_private", False)
        and getattr(event, "sender_id", None) in allowed_contact_ids
        and telegram_text(event)
    )


def _conversation_id(contact_id: str) -> str:
    return f"telegram:{contact_id}"


def _event_time(event: Any) -> str:
    value = getattr(event, "date", None)
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    return datetime.now(UTC).isoformat()


async def _event_peer(event: Any) -> Any:
    getter = getattr(event, "get_input_chat", None)
    return await getter() if getter is not None else getattr(event, "chat_id", None)


def _urgent_message(message: IncomingMessage) -> bool:
    normalized = message.text.lower()
    return any(marker in normalized for marker in ("срочно", "urgent", "asap"))


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _state_from_dict(value: dict[str, Any]) -> ConversationState:
    return ConversationState(
        contact_id=str(value.get("contact_id", "")),
        conversation_stage=str(value.get("conversation_stage", "new_contact")),
        detected_intent=str(value.get("detected_intent", "unknown")),
        active_goal=str(value.get("active_goal", "acknowledge")),
        known_facts=tuple(value.get("known_facts", [])),
        missing_information=tuple(value.get("missing_information", [])),
        objections=tuple(value.get("objections", [])),
        commitments=tuple(value.get("commitments", [])),
        next_recommended_action=str(value.get("next_recommended_action", "acknowledge")),
        human_handoff_required=bool(value.get("human_handoff_required", False)),
        confidence=float(value.get("confidence", 0)),
        pending_draft_id=value.get("pending_draft_id"),
        pending_behavior_plan_id=value.get("pending_behavior_plan_id"),
    )
