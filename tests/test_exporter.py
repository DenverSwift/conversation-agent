from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conversation_agent.training.cleaning import clean_examples, redact_text
from conversation_agent.training.exporter import build_training_examples
from conversation_agent.training.models import ContextTurn, HistoryMessage, TrainingExample

DIALOG_ID = 1751105897
OWN_USER_ID = 42
START = datetime(2026, 7, 24, 10, 0, tzinfo=UTC)


def message(
    message_id: int,
    text: str,
    *,
    outgoing: bool,
    seconds: int | None = None,
    is_service: bool = False,
    has_media: bool = False,
    is_forwarded: bool = False,
) -> HistoryMessage:
    return HistoryMessage(
        id=message_id,
        sender_id=OWN_USER_ID if outgoing else DIALOG_ID,
        text=text,
        date=START + timedelta(seconds=seconds if seconds is not None else message_id),
        outgoing=outgoing,
        is_service=is_service,
        has_media=has_media,
        is_forwarded=is_forwarded,
    )


def test_exporter_includes_human_replies_and_chronological_context() -> None:
    messages = [
        message(1, "first question", outgoing=False),
        message(2, "first answer", outgoing=True),
        message(3, "second question", outgoing=False),
        message(4, "second answer", outgoing=True),
    ]

    examples, _ = build_training_examples(
        messages,
        dialog_id=DIALOG_ID,
        own_user_id=OWN_USER_ID,
        known_ai_message_ids=set(),
        limit=500,
        context_limit=10,
    )

    assert [example.target_reply for example in examples] == ["first answer", "second answer"]
    assert [(turn.role, turn.text) for turn in examples[1].context] == [
        ("user", "first question"),
        ("assistant", "first answer"),
        ("user", "second question"),
    ]


def test_exporter_excludes_known_ai_generated_ids() -> None:
    messages = [
        message(1, "question", outgoing=False),
        message(2, "AI answer", outgoing=True),
        message(3, "human answer", outgoing=True),
    ]

    examples, stats = build_training_examples(
        messages,
        dialog_id=DIALOG_ID,
        own_user_id=OWN_USER_ID,
        known_ai_message_ids={2},
        limit=500,
        context_limit=10,
    )

    assert [example.target_reply for example in examples] == ["human answer"]
    assert stats.ai_generated_excluded == 1


def test_exporter_excludes_service_media_and_empty_messages() -> None:
    messages = [
        message(1, "", outgoing=False, is_service=True),
        message(2, "caption", outgoing=False, has_media=True),
        message(3, " ", outgoing=False),
        message(4, "question", outgoing=False),
        message(5, "answer", outgoing=True),
    ]

    examples, stats = build_training_examples(
        messages,
        dialog_id=DIALOG_ID,
        own_user_id=OWN_USER_ID,
        known_ai_message_ids=set(),
        limit=500,
        context_limit=10,
    )

    assert len(examples) == 1
    assert stats.service_messages_excluded == 1
    assert stats.media_messages_excluded == 1
    assert stats.empty_messages_excluded == 1


def test_exporter_combines_consecutive_fragments() -> None:
    messages = [
        message(1, "are we meeting?", outgoing=False, seconds=0),
        message(2, "tomorrow?", outgoing=False, seconds=20),
        message(3, "yes", outgoing=True, seconds=40),
        message(4, "at seven", outgoing=True, seconds=60),
    ]

    examples, _ = build_training_examples(
        messages,
        dialog_id=DIALOG_ID,
        own_user_id=OWN_USER_ID,
        known_ai_message_ids=set(),
        limit=500,
        context_limit=10,
    )

    assert len(examples) == 1
    assert examples[0].context == (ContextTurn(role="user", text="are we meeting?\ntomorrow?"),)
    assert examples[0].target_reply == "yes\nat seven"
    assert examples[0].source_message_ids == (3, 4)


def test_cleaning_removes_empty_and_link_only_targets() -> None:
    context = (ContextTurn(role="user", text="hello"),)
    examples = [
        training_example("empty", "", context),
        training_example("link", "https://example.com", context),
        training_example("valid", "normal answer", context),
    ]

    cleaned, stats = clean_examples(examples, redact_pii=False)

    assert [example.example_id for example in cleaned] == ["valid"]
    assert stats.examples_removed == 2


def test_pii_redaction_is_conservative() -> None:
    counts = {"email": 0, "phone": 0, "url": 0, "secret": 0}
    text = (
        "mail me at matvey@example.com or +380 67 123 45 67; "
        "open https://example.com/path?token=value and use sk-abcdefghijklmnop"
    )

    redacted = redact_text(text, counts)

    assert "<EMAIL>" in redacted
    assert "<PHONE>" in redacted
    assert "<URL>" in redacted
    assert "<SECRET>" in redacted
    assert counts == {"email": 1, "phone": 1, "url": 1, "secret": 1}


def training_example(
    example_id: str,
    target: str,
    context: tuple[ContextTurn, ...],
) -> TrainingExample:
    return TrainingExample(
        example_id=example_id,
        dialog_id=DIALOG_ID,
        context=context,
        target_reply=target,
        source_message_ids=(1,),
        created_at=START.isoformat(),
        is_human_authored=True,
    )
