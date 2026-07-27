from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import replace
from pathlib import Path

import pytest

import conversation_agent.tools.inspect_style_runtime as inspect_tool
from conversation_agent.agent.context_builder import ChatMessage
from conversation_agent.agent.responder import Responder
from conversation_agent.main import create_style_runtime
from conversation_agent.settings import Settings
from conversation_agent.storage.models import FeedbackUpdate, NewGeneratedReply
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository
from conversation_agent.style.bundle import load_style_bundle
from conversation_agent.style.compiler import build_style_bundle
from conversation_agent.style.models import StyleExample, StyleRule
from conversation_agent.style.retrieval import retrieve_examples
from conversation_agent.style.runtime import StyleRuntime

CONTACT_ID = 1751105897


class FakeAnalyzer:
    def __init__(self) -> None:
        self.batch_ids: list[list[str]] = []

    async def analyze_batch(self, examples, *, batch_number):
        self.batch_ids.append([item.example_id for item in examples])
        return [
            StyleRule(
                text=f"Use observed pattern from batch {batch_number}.",
                confidence=0.8,
                evidence_count=len(examples),
                source_type="mixed",
                applicable_context="general",
                scope="contact" if batch_number == 1 else "global",
            )
        ]

    async def merge_rules(self, rules):
        return list(rules)


def write_source(path: Path, count: int = 5) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for index in range(count):
            handle.write(
                json.dumps(
                    {
                        "example_id": f"human-{index}",
                        "dialog_id": CONTACT_ID,
                        "context": [{"role": "user", "text": f"incoming {index}"}],
                        "target_reply": f"reply {index}",
                        "created_at": f"2026-07-{index + 1:02d}T00:00:00+00:00",
                        "is_human_authored": True,
                    }
                )
                + "\n"
            )


def settings(tmp_path: Path) -> Settings:
    return Settings(
        telegram_api_id=1,
        telegram_api_hash="hash",
        telegram_session_path=".secrets/test",
        openai_api_key="key",
        openai_model="model",
        allowed_telegram_user_id=CONTACT_ID,
        context_message_limit=30,
        readme_path=tmp_path / "README.md",
        openai_timeout_seconds=30,
        style_bundle_directory=tmp_path / "style",
        style_source_examples_path=tmp_path / "cleaned_examples.jsonl",
        style_compiler_state_path=tmp_path / "style" / "compiler_state.sqlite3",
    )


def build_fixture_bundle(tmp_path: Path, *, count: int = 5):
    source = tmp_path / "cleaned_examples.jsonl"
    write_source(source, count)
    analyzer = FakeAnalyzer()
    summary = asyncio.run(
        build_style_bundle(
            source_path=source,
            output_directory=tmp_path / "style",
            contact_id=CONTACT_ID,
            source_limit=500,
            analyzer=analyzer,
            analysis_model="fake",
            batch_size=2,
        )
    )
    return load_style_bundle(tmp_path / "style", contact_id=CONTACT_ID), analyzer, summary


def test_style_compiler_processes_every_source_batch(tmp_path: Path) -> None:
    bundle, analyzer, summary = build_fixture_bundle(tmp_path)

    assert analyzer.batch_ids == [
        ["human-0", "human-1"],
        ["human-2", "human-3"],
        ["human-4"],
    ]
    assert bundle.source_example_count == 5
    assert summary["batch_count"] == 3
    assert len(bundle.examples) == 5


def test_style_compiler_ignores_repeated_row_with_same_source_key(tmp_path: Path) -> None:
    source = tmp_path / "cleaned_examples.jsonl"
    write_source(source, 2)
    lines = source.read_text(encoding="utf-8").splitlines()
    source.write_text("\n".join([lines[0], lines[0], lines[1]]) + "\n", encoding="utf-8")
    analyzer = FakeAnalyzer()

    summary = asyncio.run(
        build_style_bundle(
            source_path=source,
            output_directory=tmp_path / "style",
            contact_id=CONTACT_ID,
            source_limit=500,
            analyzer=analyzer,
            analysis_model="fake",
            batch_size=2,
        )
    )
    bundle = load_style_bundle(tmp_path / "style", contact_id=CONTACT_ID)

    assert analyzer.batch_ids == [["human-0", "human-1"]]
    assert summary["source_example_count"] == 2
    assert summary["example_bank_count"] == 2
    assert len(bundle.examples) == 2


def test_manual_overrides_are_not_overwritten(tmp_path: Path) -> None:
    source = tmp_path / "cleaned_examples.jsonl"
    style_dir = tmp_path / "style"
    write_source(source, 2)
    style_dir.mkdir()
    override = style_dir / "manual_overrides.md"
    override.write_text("Manual rule wins.", encoding="utf-8")

    asyncio.run(
        build_style_bundle(
            source_path=source,
            output_directory=style_dir,
            contact_id=CONTACT_ID,
            source_limit=500,
            analyzer=FakeAnalyzer(),
            analysis_model="fake",
            batch_size=1,
        )
    )

    assert override.read_text(encoding="utf-8") == "Manual rule wins."


def test_incomplete_compiler_run_does_not_publish_bundle(tmp_path: Path) -> None:
    class EmptyAnalyzer(FakeAnalyzer):
        async def analyze_batch(self, examples, *, batch_number):
            return []

    source = tmp_path / "cleaned_examples.jsonl"
    output = tmp_path / "style"
    write_source(source, 2)

    with pytest.raises(ValueError, match="returned no rules"):
        asyncio.run(
            build_style_bundle(
                source_path=source,
                output_directory=output,
                contact_id=CONTACT_ID,
                source_limit=500,
                analyzer=EmptyAnalyzer(),
                analysis_model="fake",
                batch_size=1,
            )
        )

    assert not (output / "matvey_behavior_rules.md").exists()


def test_retrieval_prefers_related_fix_and_preserves_profanity() -> None:
    examples = [
        StyleExample(
            "human",
            CONTACT_ID,
            "send documents",
            "okay",
            "human_matvey",
            "positive",
        ),
        StyleExample(
            "fix",
            CONTACT_ID,
            "иди на хуй",
            "сам иди нахуй",
            "fix",
            "positive",
        ),
    ]

    selected = retrieve_examples("иди на хуй", examples, contact_id=CONTACT_ID, limit=2)

    assert selected[0].example.source_type == "fix"
    assert selected[0].example.response_text == "сам иди нахуй"
    assert selected[-1].example.example_id == "human"


def test_style_runtime_includes_rules_contact_manual_and_provenance(tmp_path: Path) -> None:
    bundle, _, _ = build_fixture_bundle(tmp_path)
    (tmp_path / "style" / "manual_overrides.md").write_text(
        "MANUAL OVERRIDE SENTINEL",
        encoding="utf-8",
    )
    runtime = StyleRuntime(
        bundle=bundle,
        bundle_directory=tmp_path / "style",
        repository=None,
        contact_id=CONTACT_ID,
        retrieval_limit=2,
        rules_max_chars=12000,
        examples_max_chars=10000,
    )

    composed = runtime.compose(
        [
            ChatMessage("assistant", "old ai", "ai_generated", 20),
            ChatMessage("user", "incoming 4", "contact", 21),
        ]
    )

    assert composed.instructions.index("MANUAL OVERRIDE SENTINEL") < composed.instructions.index(
        "MATVEY BEHAVIOR RULEBOOK"
    )
    assert "Use observed pattern" in composed.instructions
    assert f'"contact_id": {CONTACT_ID}' in composed.instructions
    assert "RELEVANT REAL EXAMPLES" in composed.instructions
    assert composed.messages[0]["content"] == "old ai"
    assert composed.messages[-1]["content"] == "incoming 4"


def test_immediate_fix_is_retrieved_without_rebuild(tmp_path: Path) -> None:
    bundle, _, _ = build_fixture_bundle(tmp_path)
    repository = SQLiteFeedbackRepository(tmp_path / "feedback.sqlite3")
    repository.initialize()
    reply_id = repository.create_generated_reply(
        NewGeneratedReply(
            dialog_id=CONTACT_ID,
            incoming_message_id=10,
            created_at="2026-07-24T10:00:00+00:00",
            model="test",
            prompt_version="AAA.3",
            generated_reply_text="What is wrong?",
            context_json='[{"role":"user","text":"go away"}]',
            incoming_message_text="go away",
        )
    )
    repository.save_feedback(
        reply_id,
        FeedbackUpdate(
            status="corrected",
            updated_at="2026-07-24T10:01:00+00:00",
            corrected_reply_text="you go away",
        ),
    )
    runtime = StyleRuntime(
        bundle=bundle,
        bundle_directory=tmp_path / "style",
        repository=repository,
        contact_id=CONTACT_ID,
        retrieval_limit=2,
        rules_max_chars=12000,
        examples_max_chars=10000,
    )

    composed = runtime.compose([ChatMessage("user", "go away", "contact")])

    assert composed.selected_fix_count == 1
    assert "you go away" in composed.instructions


def test_rejected_and_approved_ai_are_not_positive_style_evidence(tmp_path: Path) -> None:
    repository = SQLiteFeedbackRepository(tmp_path / "feedback.sqlite3")
    repository.initialize()
    for index, status in enumerate(("approved", "rejected"), start=1):
        reply_id = repository.create_generated_reply(
            NewGeneratedReply(
                dialog_id=CONTACT_ID,
                incoming_message_id=index,
                created_at="2026-07-24T10:00:00+00:00",
                model="test",
                prompt_version="AAA.3",
                generated_reply_text=f"ai {status}",
                context_json='[{"role":"user","text":"hello"}]',
                incoming_message_text="hello",
            )
        )
        repository.save_feedback(
            reply_id,
            FeedbackUpdate(
                status=status,
                category="should_not_reply" if status == "rejected" else None,
                updated_at="2026-07-24T10:01:00+00:00",
            ),
        )
    bundle, _, _ = build_fixture_bundle(tmp_path)
    runtime = StyleRuntime(
        bundle=bundle,
        bundle_directory=tmp_path / "style",
        repository=repository,
        contact_id=CONTACT_ID,
        retrieval_limit=10,
        rules_max_chars=12000,
        examples_max_chars=10000,
    )

    composed = runtime.compose([ChatMessage("user", "hello", "contact")])

    assert "ai approved" not in composed.instructions
    assert "NEGATIVE SHOULD_NOT_REPLY EVIDENCE" in composed.instructions


def test_style_rules_survive_recent_history_trimming(tmp_path: Path) -> None:
    bundle, _, _ = build_fixture_bundle(tmp_path)
    runtime = StyleRuntime(
        bundle=bundle,
        bundle_directory=tmp_path / "style",
        repository=None,
        contact_id=CONTACT_ID,
        retrieval_limit=1,
        rules_max_chars=2000,
        examples_max_chars=1000,
    )
    messages = [
        ChatMessage("user", "x" * 4000, "contact", index) for index in range(10)
    ]

    composed = runtime.compose(messages)

    assert "MATVEY BEHAVIOR RULEBOOK" in composed.instructions
    assert len(composed.messages) < len(messages)


def test_required_missing_bundle_has_build_command(tmp_path: Path) -> None:
    current = replace(
        settings(tmp_path),
        style_adaptation_enabled=True,
        style_require_bundle=True,
    )

    with pytest.raises(ValueError, match=r"scripts\\build_style_bundle.bat"):
        create_style_runtime(current, None)


def test_style_bundle_is_loaded_for_live_runtime(tmp_path: Path) -> None:
    build_fixture_bundle(tmp_path)
    current = replace(
        settings(tmp_path),
        style_adaptation_enabled=True,
        style_require_bundle=True,
    )

    runtime = create_style_runtime(current, None)

    assert runtime is not None
    assert runtime.bundle.source_example_count == 5


class FakeReplyClient:
    def __init__(self) -> None:
        self.calls = []

    async def create_reply(self, *, instructions, messages):
        self.calls.append((instructions, messages))
        return "reply"


def test_disabled_responder_preserves_aaa3_request() -> None:
    client = FakeReplyClient()
    responder = Responder(client, "old instructions")

    asyncio.run(responder.reply([ChatMessage("user", "hello", "contact")]))

    assert client.calls == [
        ("old instructions", [{"role": "user", "content": "hello"}])
    ]


def test_private_prompt_content_is_not_logged(tmp_path: Path, caplog) -> None:
    bundle, _, _ = build_fixture_bundle(tmp_path)
    private_text = "PRIVATE STYLE SENTINEL"
    runtime = StyleRuntime(
        bundle=bundle,
        bundle_directory=tmp_path / "style",
        repository=None,
        contact_id=CONTACT_ID,
        retrieval_limit=1,
        rules_max_chars=12000,
        examples_max_chars=10000,
    )

    with caplog.at_level(logging.INFO):
        runtime.compose([ChatMessage("user", private_text, "contact")])

    assert private_text not in caplog.text


def test_safe_inspection_prints_metadata_only(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    build_fixture_bundle(tmp_path)
    current = replace(
        settings(tmp_path),
        style_adaptation_enabled=True,
        prompt_version="AA.2",
    )
    monkeypatch.setattr(inspect_tool.Settings, "load", lambda: current)
    monkeypatch.setattr("sys.argv", ["inspect_style_runtime"])

    assert inspect_tool.main() == 0
    captured = capsys.readouterr()

    metadata = json.loads(captured.out)
    assert metadata["prompt_version"] == "AA.2"
    assert metadata["source_example_count"] == 5
    assert "incoming 0" not in captured.out
    assert captured.err == ""
