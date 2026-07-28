from __future__ import annotations

import asyncio
import json
import random
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conversation_agent.agent.analyzer import InteractionAnalyzer
from conversation_agent.agent.goal_planner import GoalPlanner
from conversation_agent.agent.prompt_builder import load_readme_behavior
from conversation_agent.agent.prompt_composer import PromptComposer
from conversation_agent.agent.response_generator import ResponseGenerator
from conversation_agent.domain.models import (
    BusinessProfile,
    ConversationState,
    GeneratedResponse,
    IdentityProfile,
    IncomingMessage,
    InteractionAnalysis,
    RelationshipProfile,
    StyleProfile,
)
from conversation_agent.llm.conversation_client import DeterministicFakeProvider
from conversation_agent.main import simulate
from conversation_agent.storage.conversation_models import NewAgentDraft
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository
from conversation_agent.style.compiler import examples_from_feedback
from conversation_agent.telegram.approval import ApprovalActionWorker
from conversation_agent.telegram.behavior import (
    BehaviorConfig,
    TelegramBehaviorPlanner,
    TelegramBehaviorRuntime,
)
from conversation_agent.telegram.buffer import IncomingMessageBuffer
from conversation_agent.trainer.service import TrainerService

NOW = "2026-07-28T12:00:00+00:00"


class FailingProvider:
    async def generate_structured(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        raise RuntimeError("offline failure")


class NoReplyProvider:
    async def generate_structured(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        return {
            "should_reply": False,
            "messages": [],
            "tone": "neutral",
            "goal": "do_not_reply",
            "handoff_required": False,
            "confidence": 0.9,
        }


class _Typing:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *args: object) -> None:
        del args


class FakeTelegramClient:
    def __init__(
        self,
        repository: SQLiteFeedbackRepository | None = None,
        *,
        interrupt_after_first: bool = False,
    ) -> None:
        self.repository = repository
        self.interrupt_after_first = interrupt_after_first
        self.sent: list[tuple[int, str]] = []
        self.read: list[int] = []

    async def send_read_acknowledge(self, contact_id: int) -> None:
        self.read.append(contact_id)

    def action(self, contact_id: int, kind: str) -> _Typing:
        del contact_id, kind
        return _Typing()

    async def send_message(self, contact_id: int, text: str) -> Any:
        self.sent.append((contact_id, text))
        if (
            self.interrupt_after_first
            and len(self.sent) == 1
            and self.repository is not None
        ):
            self.repository.mark_pending_drafts_stale(
                str(contact_id),
                updated_at=datetime.now(UTC).isoformat(),
            )
        return type("Sent", (), {"id": 100 + len(self.sent)})()


async def no_sleep(seconds: float) -> None:
    del seconds


def repository(tmp_path: Path) -> SQLiteFeedbackRepository:
    result = SQLiteFeedbackRepository(tmp_path / "agent.sqlite3")
    result.initialize()
    return result


def create_draft(
    repo: SQLiteFeedbackRepository,
    *,
    contact_id: str = "100",
    messages: tuple[str, ...] = ("first", "second"),
) -> int:
    conversation_id = f"telegram:{contact_id}"
    repo.upsert_conversation(conversation_id, contact_id, updated_at=NOW)
    behavior = {
        "initial_read_delay_ms": 0,
        "pre_typing_delay_ms": 0,
        "typing_duration_ms": 0,
        "messages": [
            {"text": text, "delay_before_ms": 0}
            for text in messages
        ],
    }
    return repo.create_agent_draft(
        NewAgentDraft(
            conversation_id=conversation_id,
            contact_id=contact_id,
            message_group_id=f"{contact_id}:1-2",
            incoming_message_id=2,
            incoming_message_text="incoming",
            created_at=NOW,
            model="fake",
            prompt_version="AA.1",
            generated_reply_text="\n".join(messages),
            context_json="[]",
            analyzer_json=json.dumps(
                {
                    "intent": "asks_about_services",
                    "conversation_stage": "discovery",
                }
            ),
            goal_json='{"goal":"ask_clarifying_question"}',
            response_json=json.dumps(
                {
                    "should_reply": True,
                    "messages": list(messages),
                    "incoming_message_id": 2,
                }
            ),
            behavior_plan_json=json.dumps(behavior),
            prompt_inspection_json='{"estimated_tokens":100}',
            prompt_fingerprint="fingerprint",
            confidence=0.8,
            handoff_required=False,
        )
    )


def service(repo: SQLiteFeedbackRepository) -> TrainerService:
    return TrainerService(repo, trainer_user_id=7, review_chat_id=7)


def behavior_config() -> BehaviorConfig:
    return BehaviorConfig(
        typing_speed_min_chars_per_second=8,
        typing_speed_max_chars_per_second=12,
        delay_jitter_ms=0,
        initial_read_delay_min_ms=800,
        initial_read_delay_max_ms=1200,
        pre_typing_delay_min_ms=500,
        pre_typing_delay_max_ms=900,
        bubble_delay_min_ms=400,
        bubble_delay_max_ms=700,
    )


def test_message_burst_is_combined_into_one_group() -> None:
    async def scenario() -> None:
        groups = []
        buffer = IncomingMessageBuffer(
            minimum_wait_seconds=0.01,
            maximum_wait_seconds=0.05,
            on_group=lambda group: _append(groups, group),
        )
        await buffer.add(IncomingMessage(1, "100", "hello", NOW))
        await asyncio.sleep(0.005)
        await buffer.add(IncomingMessage(2, "100", "need a bot", NOW))
        await asyncio.sleep(0.03)
        assert len(groups) == 1
        assert groups[0].text == "hello\nneed a bot"

    asyncio.run(scenario())


async def _append(target: list[Any], value: Any) -> None:
    target.append(value)


def test_new_contact_gets_neutral_relationship_profile() -> None:
    profile = RelationshipProfile.neutral("new-contact")
    assert profile.contact_id == "new-contact"
    assert profile.relationship_type == "unknown"
    assert profile.confidence == 0


def test_analyzer_returns_valid_structure() -> None:
    async def scenario() -> None:
        analyzer = InteractionAnalyzer(DeterministicFakeProvider(), model="fake")
        group = _group("нужен бот")
        result = await analyzer.analyze(
            group=group,
            recent_messages=[],
            state=ConversationState.initial("100"),
            business=BusinessProfile(name="Studio"),
        )
        assert result.intent == "asks_about_services"
        assert result.recommended_goal == "qualify_timeline"
        assert 0 <= result.confidence <= 1

    asyncio.run(scenario())


def test_analyzer_failure_uses_safe_fallback() -> None:
    async def scenario() -> None:
        analyzer = InteractionAnalyzer(FailingProvider(), model="fake")
        result = await analyzer.analyze(
            group=_group("hello"),
            recent_messages=[],
            state=ConversationState.initial("100"),
            business=BusinessProfile(name="Studio"),
        )
        assert result.fallback_used is True
        assert result.recommended_goal == "ask_clarifying_question"

    asyncio.run(scenario())


def test_goal_planner_asks_to_clarify_task() -> None:
    analysis = InteractionAnalysis.safe_fallback()
    goal = GoalPlanner(handoff_threshold=0.1).plan(
        analysis,
        ConversationState.initial("100"),
        BusinessProfile(name="Studio"),
    )
    assert goal.goal == "ask_clarifying_question"


def test_response_generator_can_choose_no_reply() -> None:
    async def scenario() -> None:
        generator = ResponseGenerator(
            NoReplyProvider(),
            model="fake",
            max_bubble_count=4,
            max_message_length=100,
        )
        response = await generator.generate(
            _prompt(),
            GoalPlanner(handoff_threshold=0.1).plan(
                _analysis(recommended_goal="acknowledge"),
                ConversationState.initial("100"),
                BusinessProfile(name="Studio"),
            ),
        )
        assert response.should_reply is False
        assert response.messages == ()

    asyncio.run(scenario())


def test_long_response_is_split_into_bubbles() -> None:
    class LongProvider:
        async def generate_structured(self, **kwargs: Any) -> dict[str, Any]:
            del kwargs
            return {
                "should_reply": True,
                "messages": ["one two three four five six"],
                "tone": "neutral",
                "goal": "acknowledge",
                "handoff_required": False,
                "confidence": 0.8,
            }

    async def scenario() -> None:
        generator = ResponseGenerator(
            LongProvider(),
            model="fake",
            max_bubble_count=3,
            max_message_length=10,
        )
        response = await generator.generate(
            _prompt(),
            GoalPlanner(handoff_threshold=0.1).plan(
                _analysis(recommended_goal="acknowledge"),
                ConversationState.initial("100"),
                BusinessProfile(name="Studio"),
            ),
        )
        assert 1 < len(response.messages) <= 3
        assert all(len(item) <= 10 for item in response.messages)

    asyncio.run(scenario())


def test_behavior_delays_stay_within_configured_bounds() -> None:
    planner = TelegramBehaviorPlanner(
        behavior_config(),
        random_source=random.Random(1),
    )
    plan = planner.plan(
        GeneratedResponse(True, ("hello", "there"), "neutral", "acknowledge", False, 0.8),
        urgency=0,
        hour=12,
    )
    assert 800 <= plan.initial_read_delay_ms <= 1200
    assert 500 <= plan.pre_typing_delay_ms <= 900
    assert 400 <= plan.messages[1].delay_before_ms <= 700
    assert plan.typing_duration_ms > 0


def test_new_incoming_marks_pending_draft_stale(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    draft_id = create_draft(repo)
    assert repo.mark_pending_drafts_stale("100", updated_at=NOW) == [draft_id]
    assert repo.get_agent_draft(draft_id).status == "stale"  # type: ignore[union-attr]


def test_approve_sends_messages_in_order(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = repository(tmp_path)
        draft_id = create_draft(repo)
        service(repo).handle_callback(f"approve:{draft_id}")
        client = FakeTelegramClient()
        runtime = TelegramBehaviorRuntime(
            client=client,
            repository=repo,
            sleep=no_sleep,
        )
        worker = ApprovalActionWorker(
            repository=repo,
            behavior_runtime=runtime,
            poll_interval_seconds=0.001,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(stop))
        for _ in range(100):
            if len(client.sent) == 2:
                break
            await asyncio.sleep(0)
        stop.set()
        await task
        assert client.sent == [(100, "first"), (100, "second")]

    asyncio.run(scenario())


def test_interruption_cancels_remaining_bubbles(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = repository(tmp_path)
        draft_id = create_draft(repo)
        client = FakeTelegramClient(repo, interrupt_after_first=True)
        runtime = TelegramBehaviorRuntime(
            client=client,
            repository=repo,
            sleep=no_sleep,
        )
        sent = await runtime.execute(draft_id=draft_id)
        assert sent is False
        assert client.sent == [(100, "first")]
        assert repo.get_agent_draft(draft_id).status == "stale"  # type: ignore[union-attr]

    asyncio.run(scenario())


def test_fix_is_sent_and_stored_as_human_fix(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = repository(tmp_path)
        draft_id = create_draft(repo)
        trainer = service(repo)
        trainer.handle_callback(f"fix:{draft_id}")
        trainer.handle_text("human correction")
        action = repo.claim_next_trainer_action(claimed_at=NOW)
        assert action is not None
        runtime = TelegramBehaviorRuntime(
            client=FakeTelegramClient(),
            repository=repo,
            sleep=no_sleep,
        )
        repo.update_draft_status(draft_id, "approved", updated_at=NOW)
        assert await runtime.execute(
            draft_id=draft_id,
            corrected_text=action.payload_text,
        )
        with sqlite3.connect(repo.database_path) as connection:
            row = connection.execute(
                "SELECT provenance, text FROM messages WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
        assert row == ("human_fix", "human correction")

    asyncio.run(scenario())


def test_rejected_ai_draft_is_not_positive_style_evidence(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    draft_id = create_draft(repo)
    service(repo).handle_callback(f"reject:{draft_id}")
    examples, _ = examples_from_feedback(repo.reviewed_replies(), contact_id=100)
    assert not any(example.polarity == "positive" for example in examples)


def test_ai_sent_message_keeps_ai_provenance(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = repository(tmp_path)
        draft_id = create_draft(repo, messages=("ai draft",))
        runtime = TelegramBehaviorRuntime(
            client=FakeTelegramClient(),
            repository=repo,
            sleep=no_sleep,
        )
        assert await runtime.execute(draft_id=draft_id)
        with sqlite3.connect(repo.database_path) as connection:
            provenance = connection.execute(
                "SELECT provenance FROM messages WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()[0]
        assert provenance == "ai_sent"
        assert provenance != "imported_human_history"

    asyncio.run(scenario())


def test_handoff_disables_contact_processing(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = repository(tmp_path)
        draft_id = create_draft(repo)
        service(repo).handle_callback(f"handoff:{draft_id}")
        client = FakeTelegramClient()
        worker = ApprovalActionWorker(
            repository=repo,
            behavior_runtime=TelegramBehaviorRuntime(
                client=client,
                repository=repo,
                sleep=no_sleep,
            ),
            poll_interval_seconds=0.001,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(stop))
        for _ in range(100):
            if repo.handoff_active("100"):
                break
            await asyncio.sleep(0)
        stop.set()
        await task
        assert repo.handoff_active("100")
        assert client.sent == []

    asyncio.run(scenario())


def test_contacts_are_buffered_independently() -> None:
    async def scenario() -> None:
        completed: list[str] = []
        buffer = IncomingMessageBuffer(
            minimum_wait_seconds=0.01,
            maximum_wait_seconds=0.03,
            on_group=lambda group: _append(completed, group.contact_id),
        )
        await asyncio.gather(
            buffer.add(IncomingMessage(1, "100", "a", NOW)),
            buffer.add(IncomingMessage(1, "200", "b", NOW)),
        )
        await asyncio.sleep(0.03)
        assert set(completed) == {"100", "200"}

    asyncio.run(scenario())


def test_runtime_tasks_for_contacts_do_not_block_each_other(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = repository(tmp_path)
        first = create_draft(repo, contact_id="100", messages=("one",))
        second = create_draft(repo, contact_id="200", messages=("two",))
        assert repo.enqueue_trainer_action(first, action="approve", created_at=NOW)
        assert repo.enqueue_trainer_action(second, action="approve", created_at=NOW)
        client = FakeTelegramClient()
        both_started = asyncio.Event()
        started = 0

        async def concurrent_sleep(delay: float) -> None:
            nonlocal started
            del delay
            started += 1
            if started >= 2:
                both_started.set()
            await asyncio.wait_for(both_started.wait(), timeout=0.2)

        runtime = TelegramBehaviorRuntime(
            client=client,
            repository=repo,
            sleep=concurrent_sleep,
        )
        worker = ApprovalActionWorker(
            repository=repo,
            behavior_runtime=runtime,
            poll_interval_seconds=0.001,
        )
        stop = asyncio.Event()
        task = asyncio.create_task(worker.run(stop))
        while len(client.sent) < 2:
            await asyncio.sleep(0)
        stop.set()
        await task
        assert {contact for contact, _ in client.sent} == {100, 200}

    asyncio.run(scenario())


def test_sqlite_migration_is_idempotent_and_creates_required_tables(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    repo.initialize()
    with sqlite3.connect(repo.database_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        version = connection.execute("PRAGMA user_version").fetchone()[0]
    required = {
        "identities",
        "business_profiles",
        "style_profiles",
        "relationship_profiles",
        "conversations",
        "conversation_states",
        "messages",
        "drafts",
        "behavior_plans",
        "feedback",
        "retrieved_examples",
        "runtime_events",
        "handoffs",
    }
    assert required <= tables
    assert version == 3


def test_private_runtime_files_are_git_ignored() -> None:
    result = subprocess.run(
        [
            "git",
            "check-ignore",
            ".env",
            ".secrets/matvey.session",
            ".runtime/agent.sqlite3",
            "logs/agent.log",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    assert len(result.stdout.splitlines()) == 4


def test_simulation_runs_without_telegram_or_openai_credentials(
    monkeypatch: Any,
    capsys: Any,
) -> None:
    monkeypatch.setattr("conversation_agent.main.load_env_file", lambda path: None)
    for name in (
        "TELEGRAM_API_ID",
        "TELEGRAM_API_HASH",
        "TELEGRAM_SESSION_PATH",
        "OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)
    asyncio.run(simulate(contact_id="test-contact", message="нужен Telegram бот"))
    output = json.loads(capsys.readouterr().out)
    assert output["analyzer"]["intent"] == "asks_about_services"
    assert output["bubble_split"]


def test_llm_provider_is_replaceable_with_fake() -> None:
    provider = DeterministicFakeProvider()
    result = asyncio.run(
        provider.generate_structured(
            model="fake",
            instructions="",
            input_messages=[{"role": "user", "content": "нужен бот"}],
            schema_name="interaction_analysis",
            schema={},
        )
    )
    assert result["intent"] == "asks_about_services"


def test_repeated_trainer_callback_is_idempotent(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    draft_id = create_draft(repo)
    trainer = service(repo)
    trainer.handle_callback(f"approve:{draft_id}")
    trainer.handle_callback(f"approve:{draft_id}")
    with sqlite3.connect(repo.database_path) as connection:
        action_count = connection.execute(
            "SELECT COUNT(*) FROM trainer_actions WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()[0]
        feedback_count = connection.execute(
            "SELECT COUNT(*) FROM feedback WHERE draft_id = ?",
            (draft_id,),
        ).fetchone()[0]
    assert action_count == 1
    assert feedback_count == 1


def test_first_trainer_decision_wins_for_new_draft(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    draft_id = create_draft(repo)
    trainer = service(repo)
    trainer.handle_callback(f"approve:{draft_id}")
    result = trainer.handle_callback(f"reject:{draft_id}")
    record = repo.get_reply(draft_id)
    assert result.callback_notice == "Already handled"
    assert record is not None
    assert record.feedback_status == "approved"


def test_stale_draft_cannot_be_sent(tmp_path: Path) -> None:
    async def scenario() -> None:
        repo = repository(tmp_path)
        draft_id = create_draft(repo)
        repo.mark_pending_drafts_stale("100", updated_at=NOW)
        client = FakeTelegramClient()
        runtime = TelegramBehaviorRuntime(
            client=client,
            repository=repo,
            sleep=no_sleep,
        )
        assert await runtime.execute(draft_id=draft_id) is False
        assert client.sent == []

    asyncio.run(scenario())


def test_business_restrictions_are_in_prompt() -> None:
    composer = PromptComposer(token_budget=2000)
    prompt = composer.compose(
        identity=IdentityProfile("owner", "Owner"),
        business=BusinessProfile(
            name="Studio",
            restrictions=("Do not promise delivery dates",),
        ),
        style=StyleProfile(),
        relationship=RelationshipProfile.neutral("100"),
        state=ConversationState.initial("100"),
        analysis=_analysis(),
        goal=GoalPlanner(handoff_threshold=0.1).plan(
            _analysis(),
            ConversationState.initial("100"),
            BusinessProfile(name="Studio"),
        ),
        recent_messages=[],
        examples=[],
    )
    assert "Do not promise delivery dates" in prompt.instructions
    assert "BUSINESS_RESTRICTIONS" in prompt.instructions
    assert "RECENT_CONVERSATION_PROVENANCE" in prompt.instructions


def test_readme_behavior_can_be_injected_into_new_prompt(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text(
        "# Project\n\n## Matvey communication behavior\n\n- Keep replies short.\n",
        encoding="utf-8",
    )
    prompt = PromptComposer(token_budget=2000).compose(
        identity=IdentityProfile("owner", "Owner"),
        business=BusinessProfile(name="Studio"),
        style=StyleProfile(),
        relationship=RelationshipProfile.neutral("100"),
        state=ConversationState.initial("100"),
        analysis=_analysis(),
        goal=GoalPlanner(handoff_threshold=0.1).plan(
            _analysis(),
            ConversationState.initial("100"),
            BusinessProfile(name="Studio"),
        ),
        recent_messages=[],
        examples=[],
        compiled_style_rules=load_readme_behavior(readme),
    )
    assert "Keep replies short." in prompt.instructions


def test_low_confidence_causes_handoff() -> None:
    analysis = _analysis(confidence=0.1)
    goal = GoalPlanner(handoff_threshold=0.25).plan(
        analysis,
        ConversationState.initial("100"),
        BusinessProfile(name="Studio"),
    )
    assert goal.goal == "handoff_to_human"
    assert goal.handoff_required is True


def _group(text: str) -> Any:
    from conversation_agent.domain.models import IncomingMessageGroup

    return IncomingMessageGroup(
        group_id="100:1",
        contact_id="100",
        messages=(IncomingMessage(1, "100", text, NOW),),
        started_at=NOW,
        completed_at=NOW,
    )


def _analysis(
    *,
    recommended_goal: str = "ask_clarifying_question",
    confidence: float = 0.8,
) -> InteractionAnalysis:
    return InteractionAnalysis(
        should_reply=True,
        intent="asks_about_services",
        interaction_mode="business_inquiry",
        conversation_stage="discovery",
        urgency=0.4,
        sentiment="neutral",
        needs_empathy=False,
        needs_human_handoff=False,
        missing_information=(),
        recommended_goal=recommended_goal,
        confidence=confidence,
    )


def _prompt() -> Any:
    from conversation_agent.domain.models import PromptPackage

    return PromptPackage(
        instructions="safe",
        input_messages=({"role": "user", "content": "hello"},),
        inspection={},
        estimated_tokens=1,
    )
