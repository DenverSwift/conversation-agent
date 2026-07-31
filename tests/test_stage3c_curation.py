from __future__ import annotations

import csv
import json
import logging
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import conversation_agent.local_slm.batch_curation as batch_module
import conversation_agent.local_slm.provenance_recovery as discovery_module
from conversation_agent.local_slm.batch_curation import (
    batch_review_stats,
    build_batch_review,
    build_curated_style_profiles,
    confirm_curated_dataset,
)
from conversation_agent.local_slm.provenance_recovery import (
    discover_provenance_sources,
    inspect_sqlite_database,
    readonly_connection,
)
from conversation_agent.local_slm.telegram_curation import (
    AuditEvidence,
    ReconciliationOptions,
    TelegramCurationError,
    heuristic_review_signals,
    reconcile_episode,
    reconcile_outgoing_message,
    reconciliation_fingerprint,
)
from conversation_agent.settings import Settings

BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
FIXTURE_CHAT_ID = 41414141


def _create_audit_database(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            CREATE TABLE audit_events (
                id INTEGER PRIMARY KEY,
                chat_id INTEGER,
                telegram_message_id INTEGER,
                origin TEXT,
                timestamp TEXT,
                response_text TEXT,
                provider TEXT,
                model TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO audit_events VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                1,
                FIXTURE_CHAT_ID,
                101,
                "ai_generated",
                BASE_TIME.isoformat(),
                "synthetic generated text",
                "fixture",
                "fixture-model",
            ),
        )


def _evidence(
    classification: str,
    *,
    message_id: int | None = 101,
    chat_id: int | None = FIXTURE_CHAT_ID,
    text_hash: str | None = None,
    timestamp: str | None = None,
) -> AuditEvidence:
    return AuditEvidence(
        source_alias="database-001",
        record_id=f"record-{classification}",
        classification=classification,  # type: ignore[arg-type]
        chat_id=chat_id,
        message_id=message_id,
        timestamp=timestamp or BASE_TIME.isoformat(),
        text_hash=text_hash,
        evidence_kind="synthetic_audit",
    )


def _episode(
    index: int,
    *,
    classification: str = "unknown_historical",
) -> dict[str, Any]:
    timestamp = (BASE_TIME + timedelta(minutes=index * 5)).isoformat()
    return {
        "example_id": f"example-{index:03d}",
        "agent_id": "fixture-agent",
        "contact_alias": "contact_private_001",
        "relationship_type": "private_contact",
        "context_turns": [
            {
                "role": "contact",
                "messages": [f"context {index}"],
                "timestamps": [timestamp],
            }
        ],
        "incoming": {
            "role": "contact",
            "messages": [f"incoming {index}"],
            "timestamps": [timestamp],
        },
        "human_target": {
            "role": "human",
            "messages": [f"human fixture response {index}"],
            "timestamps": [timestamp],
        },
        "source_type": "imported_human_candidate",
        "semantic_plan": None,
        "semantic_enrichment_status": "pending",
        "provenance": {
            "message_ids": [str(1000 + index)],
        },
        "privacy": {
            "pii_detected": False,
            "redactions": [],
            "review_required": True,
        },
        "quality_flags": [],
        "stage3c": {
            "classification": classification,
            "confidence": 0.0,
            "authoritative": classification != "unknown_historical",
            "heuristic_flags": [],
            "review_priority": 25,
        },
        "positive_human_target": classification
        in {"human_confirmed", "human_edited_ai"},
        "style_evidence_eligible": classification
        in {"human_confirmed", "human_edited_ai"},
    }


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(item) for item in rows) + ("\n" if rows else ""),
        encoding="utf-8",
    )


def _synthetic_reconciliation(
    root: Path,
    episodes: list[dict[str, Any]],
    *,
    pii_records: list[dict[str, Any]] | None = None,
    preview_fingerprint: str = "preview-fixture",
) -> tuple[Path, str]:
    reconciliation = root / "reconciliation"
    reconciliation.mkdir(parents=True)
    manifest = {
        "source_preview_fingerprint": preview_fingerprint,
        "reconciliation_fingerprint": None,
    }
    _write_json(reconciliation / "manifest.json", manifest)
    _write_jsonl(reconciliation / "messages.reconciled.jsonl", [])
    _write_jsonl(reconciliation / "episodes.reconciled.jsonl", episodes)
    _write_json(
        reconciliation / "contamination-report.json",
        {"heuristics_are_authoritative": False},
    )
    _write_json(reconciliation / "provenance-summary.json", {})
    _write_jsonl(reconciliation / "pii-records.jsonl", pii_records or [])
    fingerprint = reconciliation_fingerprint(reconciliation)
    manifest["reconciliation_fingerprint"] = fingerprint
    _write_json(reconciliation / "manifest.json", manifest)
    (reconciliation / "reconciliation-fingerprint.txt").write_text(
        fingerprint + "\n",
        encoding="utf-8",
    )
    return reconciliation, fingerprint


def _preview_fixture(root: Path, fingerprint: str = "preview-fixture") -> Path:
    preview = root / "preview"
    preview.mkdir(parents=True)
    (preview / "preview-fingerprint.txt").write_text(
        fingerprint + "\n",
        encoding="utf-8",
    )
    return preview


def _approve_all_batches(path: Path) -> None:
    rows: list[dict[str, str]]
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [dict(item) for item in csv.DictReader(handle)]
    for row in rows:
        row["decision"] = "include_human"
        row["consent_ok"] = "yes"
        row["privacy_ok"] = "yes"
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def test_discovery_lists_worktrees_and_never_scans_whole_disk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = tmp_path / "repo"
    other = tmp_path / "other-worktree"
    (repo / ".git").mkdir(parents=True)
    other.mkdir()
    _create_audit_database(repo / ".runtime" / "feedback.sqlite3")
    _create_audit_database(other / ".runtime" / "trainer.db")
    _create_audit_database(repo / "outside-approved-roots.sqlite3")
    monkeypatch.setattr(
        discovery_module,
        "git_worktrees",
        lambda _: [(repo, "feature"), (other, "main")],
    )
    output = tmp_path / "discovery"
    result = discover_provenance_sources(
        repo_root=repo,
        output=output,
        include_git_worktrees=True,
    )
    assert result["worktrees"] == 2
    assert result["candidate_databases"] == 2
    assert result["whole_disk_scanned"] is False
    report = json.loads((output / "discovery-report.json").read_text("utf-8"))
    assert all("outside-approved-roots" not in item["relative_path"] for item in report["databases"])


def test_sqlite_inspection_is_read_only_and_logs_no_text(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    database = tmp_path / "audit.sqlite3"
    _create_audit_database(database)
    caplog.set_level(logging.DEBUG)
    inspection = inspect_sqlite_database(
        database,
        alias="database-001",
        root_alias="fixture-root",
        relative_path=".runtime/audit.sqlite3",
    )
    assert inspection.sqlite_valid is True
    assert inspection.has_telegram_message_ids is True
    assert "synthetic generated text" not in caplog.text
    with readonly_connection(database) as connection, pytest.raises(sqlite3.OperationalError):
        connection.execute("INSERT INTO audit_events(origin) VALUES ('human')")


@pytest.mark.parametrize(
    ("classification", "expected"),
    (
        ("ai_generated", "ai_generated"),
        ("human_confirmed", "human_confirmed"),
        ("human_edited_ai", "human_edited_ai"),
    ),
)
def test_exact_chat_message_mapping_is_authoritative(
    classification: str,
    expected: str,
) -> None:
    result = reconcile_outgoing_message(
        message_id=101,
        chat_id=FIXTURE_CHAT_ID,
        timestamp=BASE_TIME.isoformat(),
        text="fixture",
        evidence=[_evidence(classification)],
        original_classification="unknown_historical",
        ai_operation_start=None,
    )
    assert result["classification"] == expected
    assert result["match_method"] == "exact_chat_and_message_id"
    assert result["confidence"] == 1.0


def test_text_only_match_is_secondary_and_unknown() -> None:
    text = "exact fixture text"
    text_hash = discovery_module.hashlib.sha256(
        " ".join(text.casefold().split()).encode("utf-8")
    ).hexdigest()
    result = reconcile_outgoing_message(
        message_id=999,
        chat_id=FIXTURE_CHAT_ID,
        timestamp=BASE_TIME.isoformat(),
        text=text,
        evidence=[
            _evidence(
                "ai_generated",
                message_id=None,
                chat_id=None,
                text_hash=text_hash,
            )
        ],
        original_classification="unknown_historical",
        ai_operation_start=None,
    )
    assert result["classification"] == "unknown_historical"
    assert result["match_method"] == "secondary_only"


def test_conflict_unknown_temporal_and_heuristics_never_invent_verdict() -> None:
    conflict = reconcile_outgoing_message(
        message_id=101,
        chat_id=FIXTURE_CHAT_ID,
        timestamp=BASE_TIME.isoformat(),
        text="fixture",
        evidence=[_evidence("ai_generated"), _evidence("human_confirmed")],
        original_classification="unknown_historical",
        ai_operation_start=None,
    )
    assert conflict["classification"] == "conflicting_evidence"

    unknown = reconcile_outgoing_message(
        message_id=999,
        chat_id=FIXTURE_CHAT_ID,
        timestamp=BASE_TIME.isoformat(),
        text="If you want, what exactly happened?",
        evidence=[],
        original_classification="unknown_historical",
        ai_operation_start=BASE_TIME + timedelta(days=1),
    )
    assert unknown["classification"] == "unknown_historical"
    assert unknown["temporal_signal"] == "before_user_provided_ai_start"
    assert unknown["heuristic_flags"]
    assert unknown["heuristics_changed_classification"] is False
    assert heuristic_review_signals(unknown["heuristic_flags"][0], [])[0] is not None


def test_ai_episode_is_not_human_target_and_unknown_needs_review() -> None:
    source = _episode(1)
    source["provenance"]["message_ids"] = ["101"]
    ai = reconcile_episode(
        source,
        {
            101: {
                "classification": "ai_generated",
                "record_id": "record-ai",
                "confidence": 1.0,
                "heuristic_flags": [],
                "review_priority": 0,
            }
        },
    )
    assert ai["positive_human_target"] is False
    assert ai["style_evidence_eligible"] is False
    assert "ai_contamination_excluded" in ai["quality_flags"]
    assert ai["stage3c"]["classification"] == "ai_generated"
    unknown = reconcile_episode(source, {})
    assert unknown["stage3c"]["classification"] == "unknown_historical"
    assert unknown["positive_human_target"] is False


def test_batch_review_is_deterministic_and_empty_is_not_approval(
    tmp_path: Path,
) -> None:
    episodes = [_episode(index) for index in range(1, 61)]
    reconciliation, fingerprint = _synthetic_reconciliation(tmp_path, episodes)
    first = tmp_path / "batch-one"
    second = tmp_path / "batch-two"
    result_one = build_batch_review(
        reconciliation=reconciliation,
        output=first,
        max_batch_size=25,
    )
    result_two = build_batch_review(
        reconciliation=reconciliation,
        output=second,
        max_batch_size=25,
    )
    assert result_one["batch_count"] == result_two["batch_count"] == 3
    assert result_one["reconciliation_fingerprint"] == fingerprint
    assert (first / "batch-decisions.csv").read_text("utf-8") == (
        second / "batch-decisions.csv"
    ).read_text("utf-8")
    stats = batch_review_stats(first)
    assert stats["approved_human_batches"] == 0
    assert stats["decisions"]["pending"] == 3
    assert stats["empty_is_approval"] is False


def test_curated_confirmation_gates_pii_fingerprint_and_consent(
    tmp_path: Path,
) -> None:
    episodes = [_episode(index) for index in range(1, 61)]
    pii = [
        {
            "record_id": "pii-001",
            "episode_ids": ["example-001"],
            "pii_type": "phone",
            "suggested_action": "redact",
        }
    ]
    reconciliation, fingerprint = _synthetic_reconciliation(
        tmp_path,
        episodes,
        pii_records=pii,
    )
    preview = _preview_fixture(tmp_path)
    review = tmp_path / "batch"
    build_batch_review(
        reconciliation=reconciliation,
        output=review,
        max_batch_size=25,
    )
    _approve_all_batches(review / "batch-decisions.csv")
    kwargs = {
        "preview": preview,
        "reconciliation": reconciliation,
        "batch_decisions": review / "batch-decisions.csv",
        "pii_decisions": review / "pii-review.csv",
        "fingerprint": fingerprint,
        "max_examples": 50,
        "dataset_root": tmp_path / "dataset",
    }
    with pytest.raises(TelegramCurationError, match="consent"):
        confirm_curated_dataset(**kwargs, consent_confirmed=False)
    with pytest.raises(TelegramCurationError, match="PII"):
        confirm_curated_dataset(**kwargs, consent_confirmed=True)
    kwargs["fingerprint"] = "0" * 64
    with pytest.raises(TelegramCurationError, match="fingerprint"):
        confirm_curated_dataset(**kwargs, consent_confirmed=True)


def test_curated_confirmation_caps_pilot_pseudonymizes_and_builds_profiles(
    tmp_path: Path,
) -> None:
    episodes = [_episode(index) for index in range(1, 61)]
    reconciliation, fingerprint = _synthetic_reconciliation(tmp_path, episodes)
    preview = _preview_fixture(tmp_path)
    review = tmp_path / "batch"
    build_batch_review(
        reconciliation=reconciliation,
        output=review,
        max_batch_size=25,
    )
    _approve_all_batches(review / "batch-decisions.csv")
    result = confirm_curated_dataset(
        preview=preview,
        reconciliation=reconciliation,
        batch_decisions=review / "batch-decisions.csv",
        pii_decisions=review / "pii-review.csv",
        fingerprint=fingerprint,
        consent_confirmed=True,
        max_examples=50,
        dataset_root=tmp_path / "dataset",
    )
    assert result["examples"] == 50
    dataset = Path(result["dataset"])
    serialized = (dataset / "examples.jsonl").read_text("utf-8")
    assert str(FIXTURE_CHAT_ID) not in serialized
    assert "message_ids" not in serialized
    assert "ai_generated" not in serialized
    profiles = build_curated_style_profiles(
        dataset=dataset,
        output=tmp_path / "profiles",
    )
    assert profiles["eligible_human_examples"] == 50
    assert profiles["profiles_are_distributions"] is True
    assert profiles["fixed_rules"] == []


def test_benchmark_fingerprint_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    benchmark_fingerprint = "benchmark-fixture"
    episodes = [_episode(1, classification="human_confirmed")]
    reconciliation, fingerprint = _synthetic_reconciliation(
        tmp_path,
        episodes,
        preview_fingerprint=benchmark_fingerprint,
    )
    preview = _preview_fixture(tmp_path, benchmark_fingerprint)
    review = tmp_path / "batch"
    build_batch_review(
        reconciliation=reconciliation,
        output=review,
        max_batch_size=25,
    )
    monkeypatch.setattr(
        batch_module,
        "registered_benchmark_fingerprints",
        lambda: {benchmark_fingerprint},
    )
    with pytest.raises(TelegramCurationError, match="benchmark"):
        confirm_curated_dataset(
            preview=preview,
            reconciliation=reconciliation,
            batch_decisions=review / "batch-decisions.csv",
            pii_decisions=review / "pii-review.csv",
            fingerprint=fingerprint,
            consent_confirmed=True,
            max_examples=50,
            dataset_root=tmp_path / "dataset",
        )


def test_stage3c_commands_do_not_change_production_defaults() -> None:
    assert Settings.__dataclass_fields__["generation_mode"].default == "openai_only"
    assert ReconciliationOptions(
        preview=Path("preview"),
        discovery=Path("discovery"),
        output=Path("output"),
        read_only=True,
    ).read_only is True
