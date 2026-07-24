from __future__ import annotations

import asyncio
import hashlib
import json
from collections.abc import Sequence
from pathlib import Path

import pytest

from conversation_agent.storage.models import FeedbackUpdate, NewGeneratedReply
from conversation_agent.storage.sqlite_repository import SQLiteFeedbackRepository
from conversation_agent.style.bundle import load_style_bundle
from conversation_agent.style.compiler import build_style_bundle
from conversation_agent.style.compiler_state import load_compiler_state
from conversation_agent.style.models import StyleExample, StyleRule

CONTACT_ID = 1751105897


class RecordingAnalyzer:
    def __init__(self, *, equivalent: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.equivalent = equivalent

    @property
    def request_count(self) -> int:
        return len(self.calls)

    async def analyze_batch(
        self,
        examples: Sequence[StyleExample],
        *,
        batch_number: int,
    ) -> list[StyleRule]:
        self.calls.append([item.source_key for item in examples])
        return [
            StyleRule(
                text=(
                    "Use concise replies."
                    if self.equivalent
                    else f"Use response pattern {item.response_text}."
                ),
                confidence=0.7,
                evidence_count=1,
                source_type=item.source_type,
                applicable_context="general",
                scope="contact",
                behavior_category="reply_length" if self.equivalent else item.example_id,
                supporting_source_keys=(item.source_key,),
                supporting_source_hashes=(item.content_hash,),
                polarity=item.polarity,
            )
            for item in examples
        ]


class FailingAnalyzer(RecordingAnalyzer):
    async def analyze_batch(
        self,
        examples: Sequence[StyleExample],
        *,
        batch_number: int,
    ) -> list[StyleRule]:
        self.calls.append([item.source_key for item in examples])
        raise RuntimeError("mock delta failure")


def _record(
    source_id: int,
    incoming: str,
    reply: str,
) -> dict[str, object]:
    return {
        "example_id": f"human-{source_id}",
        "dialog_id": CONTACT_ID,
        "context": [{"role": "user", "text": incoming}],
        "target_reply": reply,
        "source_message_ids": [source_id],
        "created_at": "2026-07-24T00:00:00+00:00",
        "is_human_authored": True,
    }


def _write(path: Path, records: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in records),
        encoding="utf-8",
    )


def _artifacts(style: Path) -> dict[str, str]:
    paths = [
        style / "matvey_behavior_rules.md",
        style / "style_profile.json",
        style / "example_bank.jsonl",
        style / "build_summary.json",
        style / "contacts" / f"{CONTACT_ID}.json",
    ]
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in paths
    }


def _build(
    tmp_path: Path,
    analyzer: RecordingAnalyzer | None,
    *,
    feedback_records=(),
    model: str = "fake",
    full_rebuild: bool = False,
) -> dict[str, object]:
    return asyncio.run(
        build_style_bundle(
            source_path=tmp_path / "source.jsonl",
            output_directory=tmp_path / "style",
            state_path=tmp_path / "style" / "compiler_state.sqlite3",
            contact_id=CONTACT_ID,
            source_limit=500,
            analyzer=analyzer,
            analysis_model=model,
            feedback_records=feedback_records,
            batch_size=50,
            full_rebuild=full_rebuild,
        )
    )


def test_initial_then_identical_build_is_no_op(tmp_path: Path) -> None:
    records = [_record(1, "one", "reply one"), _record(2, "two", "reply two")]
    _write(tmp_path / "source.jsonl", records)
    first = RecordingAnalyzer()

    initial = _build(tmp_path, first)
    before = _artifacts(tmp_path / "style")
    second = RecordingAnalyzer()
    no_op = _build(tmp_path, second)

    assert initial["build_mode"] == "full"
    assert first.request_count == 1
    assert no_op["build_mode"] == "no_op"
    assert no_op["OpenAI_request_count"] == 0
    assert second.request_count == 0
    assert _artifacts(tmp_path / "style") == before


def test_new_source_analyzes_only_delta(tmp_path: Path) -> None:
    records = [_record(1, "one", "reply one"), _record(2, "two", "reply two")]
    _write(tmp_path / "source.jsonl", records)
    _build(tmp_path, RecordingAnalyzer())
    _write(tmp_path / "source.jsonl", [*records, _record(3, "three", "reply three")])
    analyzer = RecordingAnalyzer()

    summary = _build(tmp_path, analyzer)

    assert analyzer.calls == [[f"telegram:{CONTACT_ID}:3"]]
    assert summary["new_sources"] == 1
    assert summary["cached_analyses_reused"] == 2


def test_duplicate_content_reuses_cached_analysis_and_counts_evidence(
    tmp_path: Path,
) -> None:
    first = _record(1, "same", "same reply")
    _write(tmp_path / "source.jsonl", [first])
    _build(tmp_path, RecordingAnalyzer(equivalent=True))
    duplicate = _record(2, "same", "same reply")
    _write(tmp_path / "source.jsonl", [first, duplicate])
    analyzer = RecordingAnalyzer(equivalent=True)

    summary = _build(tmp_path, analyzer)
    state = load_compiler_state(tmp_path / "style" / "compiler_state.sqlite3")
    profile = json.loads((tmp_path / "style" / "style_profile.json").read_text("utf-8"))

    assert analyzer.request_count == 0
    assert summary["duplicate_sources_reusing_analysis"] == 1
    assert state is not None and len(state.sources) == 2
    assert profile["rules"][0]["evidence_count"] == 2
    assert profile["rules"][0]["confidence"] == 0.73


def test_modified_source_replaces_only_old_contribution(tmp_path: Path) -> None:
    records = [_record(1, "one", "old"), _record(2, "two", "stable")]
    _write(tmp_path / "source.jsonl", records)
    _build(tmp_path, RecordingAnalyzer())
    _write(tmp_path / "source.jsonl", [_record(1, "one", "new"), records[1]])
    analyzer = RecordingAnalyzer()

    summary = _build(tmp_path, analyzer)
    rules = (tmp_path / "style" / "matvey_behavior_rules.md").read_text("utf-8")

    assert analyzer.calls == [[f"telegram:{CONTACT_ID}:1"]]
    assert summary["modified_sources"] == 1
    assert "pattern old" not in rules
    assert "pattern new" in rules
    assert "pattern stable" in rules


def test_deleted_source_removes_rule_without_reanalysis(tmp_path: Path) -> None:
    records = [_record(1, "one", "remove me"), _record(2, "two", "keep me")]
    _write(tmp_path / "source.jsonl", records)
    _build(tmp_path, RecordingAnalyzer())
    _write(tmp_path / "source.jsonl", [records[1]])
    analyzer = RecordingAnalyzer()

    summary = _build(tmp_path, analyzer)
    rules = (tmp_path / "style" / "matvey_behavior_rules.md").read_text("utf-8")

    assert analyzer.request_count == 0
    assert summary["deleted_sources"] == 1
    assert "remove me" not in rules
    assert "keep me" in rules


def test_new_fix_is_incremental_and_has_highest_priority(tmp_path: Path) -> None:
    _write(tmp_path / "source.jsonl", [_record(1, "one", "reply")])
    _build(tmp_path, RecordingAnalyzer())
    repository = SQLiteFeedbackRepository(tmp_path / "feedback.sqlite3")
    repository.initialize()
    reply_id = repository.create_generated_reply(
        NewGeneratedReply(
            dialog_id=CONTACT_ID,
            incoming_message_id=10,
            created_at="2026-07-24T00:00:00+00:00",
            model="fake",
            prompt_version="AA.2",
            generated_reply_text="wrong",
            context_json='[{"role":"user","text":"question"}]',
            incoming_message_text="question",
        )
    )
    repository.save_feedback(
        reply_id,
        FeedbackUpdate(
            status="corrected",
            corrected_reply_text="human fix",
            updated_at="2026-07-24T00:01:00+00:00",
        ),
    )
    analyzer = RecordingAnalyzer()

    summary = _build(
        tmp_path,
        analyzer,
        feedback_records=repository.reviewed_replies(),
    )
    state = load_compiler_state(tmp_path / "style" / "compiler_state.sqlite3")

    assert analyzer.calls == [[f"feedback:{reply_id}:fix"]]
    assert summary["new_sources"] == 1
    assert state is not None
    fix = state.sources[f"feedback:{reply_id}:fix"]
    assert fix.observations[0].source_priority == 100


def test_approved_ai_feedback_is_registered_without_style_analysis(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "source.jsonl", [_record(1, "one", "reply")])
    _build(tmp_path, RecordingAnalyzer())
    repository = SQLiteFeedbackRepository(tmp_path / "feedback.sqlite3")
    repository.initialize()
    reply_id = repository.create_generated_reply(
        NewGeneratedReply(
            dialog_id=CONTACT_ID,
            incoming_message_id=10,
            created_at="2026-07-24T00:00:00+00:00",
            model="fake",
            prompt_version="AA.2",
            generated_reply_text="approved ai text",
            context_json='[{"role":"user","text":"question"}]',
            incoming_message_text="question",
        )
    )
    repository.save_feedback(
        reply_id,
        FeedbackUpdate(
            status="approved",
            updated_at="2026-07-24T00:01:00+00:00",
        ),
    )
    analyzer = RecordingAnalyzer()

    summary = _build(
        tmp_path,
        analyzer,
        feedback_records=repository.reviewed_replies(),
    )
    state = load_compiler_state(tmp_path / "style" / "compiler_state.sqlite3")

    assert analyzer.request_count == 0
    assert summary["new_sources"] == 1
    assert state is not None
    approved = state.sources[f"feedback:{reply_id}:good"]
    assert approved.observations == ()


def test_fingerprint_change_requires_explicit_full_rebuild(tmp_path: Path) -> None:
    _write(tmp_path / "source.jsonl", [_record(1, "one", "reply")])
    _build(tmp_path, RecordingAnalyzer(), model="model-one")
    analyzer = RecordingAnalyzer()

    with pytest.raises(ValueError, match="--full-rebuild"):
        _build(tmp_path, analyzer, model="model-two")

    assert analyzer.request_count == 0


def test_full_rebuild_reanalyzes_and_replaces_state_after_success(
    tmp_path: Path,
) -> None:
    records = [_record(1, "one", "reply one"), _record(2, "two", "reply two")]
    _write(tmp_path / "source.jsonl", records)
    _build(tmp_path, RecordingAnalyzer(), model="old")
    analyzer = RecordingAnalyzer()

    summary = _build(
        tmp_path,
        analyzer,
        model="new",
        full_rebuild=True,
    )
    state = load_compiler_state(tmp_path / "style" / "compiler_state.sqlite3")

    assert analyzer.request_count == 1
    assert len(analyzer.calls[0]) == 2
    assert summary["build_mode"] == "full"
    assert state is not None and state.metadata["analyzer_model"] == "new"


def test_failed_delta_preserves_bundle_and_pending_source(tmp_path: Path) -> None:
    records = [_record(1, "one", "reply one")]
    _write(tmp_path / "source.jsonl", records)
    _build(tmp_path, RecordingAnalyzer())
    before = _artifacts(tmp_path / "style")
    before_state = (tmp_path / "style" / "compiler_state.sqlite3").read_bytes()
    _write(tmp_path / "source.jsonl", [*records, _record(2, "two", "reply two")])

    failed = FailingAnalyzer()
    with pytest.raises(RuntimeError, match="mock delta failure"):
        _build(tmp_path, failed)

    assert failed.request_count == 1
    assert _artifacts(tmp_path / "style") == before
    assert (tmp_path / "style" / "compiler_state.sqlite3").read_bytes() == before_state
    retry = RecordingAnalyzer()
    _build(tmp_path, retry)
    assert retry.calls == [[f"telegram:{CONTACT_ID}:2"]]


def test_equivalent_observations_merge_without_duplicate_rules(tmp_path: Path) -> None:
    _write(
        tmp_path / "source.jsonl",
        [_record(1, "one", "short"), _record(2, "two", "brief")],
    )

    _build(tmp_path, RecordingAnalyzer(equivalent=True))
    profile = json.loads((tmp_path / "style" / "style_profile.json").read_text("utf-8"))

    assert len(profile["rules"]) == 1
    assert profile["rules"][0]["evidence_count"] == 2
    assert len(profile["rules"][0]["supporting_source_keys"]) == 2


def test_existing_bundle_without_state_migrates_only_after_success(
    tmp_path: Path,
) -> None:
    style = tmp_path / "style"
    style.mkdir()
    old_rulebook = style / "matvey_behavior_rules.md"
    old_rulebook.write_text("AA.1 SENTINEL", encoding="utf-8")
    _write(tmp_path / "source.jsonl", [_record(1, "one", "reply")])

    with pytest.raises(RuntimeError):
        _build(tmp_path, FailingAnalyzer())

    assert old_rulebook.read_text("utf-8") == "AA.1 SENTINEL"
    assert not (style / "compiler_state.sqlite3").exists()
    summary = _build(tmp_path, RecordingAnalyzer())
    assert summary["build_mode"] == "full"


def test_private_text_is_absent_from_safe_summary(tmp_path: Path) -> None:
    private = "PRIVATE-SOURCE-SENTINEL"
    _write(tmp_path / "source.jsonl", [_record(1, private, "private reply")])

    summary = _build(tmp_path, RecordingAnalyzer())

    assert private not in json.dumps(summary)
    assert "private reply" not in json.dumps(summary)


def test_runtime_uses_atomic_state_artifacts_when_mirror_is_damaged(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "source.jsonl", [_record(1, "one", "reply")])
    _build(tmp_path, RecordingAnalyzer())
    (tmp_path / "style" / "style_profile.json").write_text(
        "damaged mirror",
        encoding="utf-8",
    )

    bundle = load_style_bundle(tmp_path / "style", contact_id=CONTACT_ID)

    assert bundle.source_example_count == 1
    assert bundle.rules[0].text == "Use response pattern reply."


def test_dry_run_is_private_and_does_not_create_state(tmp_path: Path) -> None:
    private = "DRY-RUN-PRIVATE"
    _write(tmp_path / "source.jsonl", [_record(1, private, "private reply")])

    summary = asyncio.run(
        build_style_bundle(
            source_path=tmp_path / "source.jsonl",
            output_directory=tmp_path / "style",
            state_path=tmp_path / "style" / "compiler_state.sqlite3",
            contact_id=CONTACT_ID,
            source_limit=500,
            analyzer=None,
            analysis_model="fake",
            batch_size=50,
            dry_run=True,
        )
    )

    serialized = json.dumps(summary)
    assert summary["new"] == 1
    assert summary["expected_analysis_batches"] == 1
    assert summary["OpenAI_calls_required"] is True
    assert private not in serialized
    assert "private reply" not in serialized
    assert not (tmp_path / "style" / "compiler_state.sqlite3").exists()


def test_disabled_incremental_mode_requires_explicit_full_rebuild(
    tmp_path: Path,
) -> None:
    _write(tmp_path / "source.jsonl", [_record(1, "one", "reply")])

    with pytest.raises(ValueError, match="--full-rebuild"):
        asyncio.run(
            build_style_bundle(
                source_path=tmp_path / "source.jsonl",
                output_directory=tmp_path / "style",
                contact_id=CONTACT_ID,
                source_limit=500,
                analyzer=RecordingAnalyzer(),
                analysis_model="fake",
                incremental=False,
            )
        )


def test_compiler_state_and_generated_files_are_git_ignored() -> None:
    ignore = Path(".gitignore").read_text(encoding="utf-8")

    assert ".runtime/" in ignore
    assert "*.sqlite3" in ignore
