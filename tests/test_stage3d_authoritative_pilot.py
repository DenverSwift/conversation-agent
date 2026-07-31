from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from conversation_agent.local_slm.authoritative_pilot import (
    recommend_pii_actions,
    recommended_pii_action,
    resolve_profile_preview,
    select_authoritative_pilot,
    selection_fingerprint,
)
from conversation_agent.local_slm.batch_curation import (
    build_curated_style_profiles,
    confirm_curated_dataset,
)
from conversation_agent.local_slm.stage2_dataset import stable_fingerprint
from conversation_agent.local_slm.telegram_curation import (
    TelegramCurationError,
    reconciliation_fingerprint,
)
from conversation_agent.settings import Settings

BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _episode(
    index: int,
    *,
    classification: str = "human_confirmed",
) -> dict[str, Any]:
    timestamp = (BASE_TIME + timedelta(minutes=index)).isoformat()
    targets = (
        ["опчтка БЛЯ", "второй bubble"]
        if index % 7 == 0
        else [f"human Response {index}?" if index % 3 == 0 else f"ответ {index}"]
    )
    if index == 5:
        targets = ["длинный " + ("технический ответ " * 16)]
    return {
        "example_id": f"example-{index:03d}",
        "agent_id": "fixture-agent",
        "contact_alias": "contact_private_001",
        "relationship_type": "private_contact",
        "context_turns": [
            {
                "role": "contact",
                "messages": [f"contact context {index}"],
                "timestamps": [timestamp],
            }
        ],
        "incoming": {
            "role": "contact",
            "messages": [f"contact incoming {index}"],
            "timestamps": [timestamp],
        },
        "human_target": {
            "role": "human",
            "messages": targets,
            "timestamps": [timestamp] * len(targets),
            "inter_bubble_delays_seconds": [2.0] if len(targets) > 1 else [],
        },
        "source_type": "imported_human_candidate",
        "provenance": {
            "message_ids": [str(1000 + index)],
            "classification": classification,
            "verified": classification in {"human_confirmed", "human_edited_ai"},
        },
        "privacy": {"pii_detected": False, "redactions": []},
        "quality_flags": [],
        "stage3c": {
            "classification": classification,
            "authoritative": classification in {"human_confirmed", "human_edited_ai"},
            "message_record_ids": [f"record-{index:03d}"],
        },
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(json.dumps(item, ensure_ascii=False) for item in rows)
        + ("\n" if rows else ""),
        encoding="utf-8",
    )


def _write_csv(
    path: Path,
    fields: tuple[str, ...],
    rows: list[dict[str, Any]],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def _reconciliation(
    root: Path,
    episodes: list[dict[str, Any]],
    pii: list[dict[str, Any]] | None = None,
) -> tuple[Path, str]:
    path = root / "reconciliation"
    path.mkdir(parents=True)
    _write_json(
        path / "manifest.json",
        {"source_preview_fingerprint": "preview-fixture"},
    )
    _write_jsonl(path / "messages.reconciled.jsonl", [])
    _write_jsonl(path / "episodes.reconciled.jsonl", episodes)
    _write_json(path / "contamination-report.json", {})
    _write_json(path / "provenance-summary.json", {})
    _write_jsonl(path / "pii-records.jsonl", pii or [])
    fingerprint = reconciliation_fingerprint(path)
    _write_json(
        path / "manifest.json",
        {
            "source_preview_fingerprint": "preview-fixture",
            "reconciliation_fingerprint": fingerprint,
        },
    )
    (path / "reconciliation-fingerprint.txt").write_text(
        fingerprint + "\n",
        encoding="utf-8",
    )
    return path, fingerprint


def _preview(root: Path) -> Path:
    path = root / "preview"
    path.mkdir()
    (path / "preview-fingerprint.txt").write_text(
        "preview-fixture\n",
        encoding="utf-8",
    )
    return path


def _manual_selection(
    path: Path,
    episodes: list[dict[str, Any]],
    reconciliation_fp: str,
) -> str:
    path.mkdir(parents=True)
    selected = [
        {
            **item,
            "source_reconciliation_fingerprint": reconciliation_fp,
        }
        for item in episodes
    ]
    diversity = {
        "selected": len(selected),
        "categories": {},
        "source_reconciliation_fingerprint": reconciliation_fp,
    }
    _write_jsonl(path / "selected.preview.jsonl", selected)
    _write_json(path / "diversity-report.json", diversity)
    fingerprint = stable_fingerprint(
        {
            "source_reconciliation": reconciliation_fp,
            "selected": selected,
            "diversity": diversity,
            "authoritative_only": True,
        }
    )
    (path / "selection-fingerprint.txt").write_text(
        fingerprint + "\n",
        encoding="utf-8",
    )
    return fingerprint


def test_pii_recommendations_are_deterministic_and_scoped(tmp_path: Path) -> None:
    episodes = [
        _episode(1),
        _episode(2, classification="ai_generated"),
        _episode(3, classification="unknown_historical"),
    ]
    pii = [
        {
            "record_id": "human-phone",
            "pii_type": "phone",
            "episode_ids": ["example-001"],
        },
        {
            "record_id": "ai-token",
            "pii_type": "api_key",
            "episode_ids": ["example-002"],
        },
        {
            "record_id": "unknown-user",
            "pii_type": "telegram_username",
            "episode_ids": ["example-003"],
        },
        {"record_id": "excluded-email", "pii_type": "email", "episode_ids": []},
    ]
    reconciliation, _ = _reconciliation(tmp_path, episodes, pii)
    review = tmp_path / "review.csv"
    _write_csv(
        review,
        ("record_id", "pii_type"),
        [
            {"record_id": item["record_id"], "pii_type": item["pii_type"]}
            for item in pii
        ],
    )
    first = tmp_path / "one" / "pii.csv"
    second = tmp_path / "two" / "pii.csv"
    result = recommend_pii_actions(
        review=review,
        reconciliation=reconciliation,
        output=first,
    )
    recommend_pii_actions(
        review=review,
        reconciliation=reconciliation,
        output=second,
    )
    assert first.read_text("utf-8") == second.read_text("utf-8")
    assert result["scope_counts"] == {
        "ai": 1,
        "authoritative_human": 1,
        "excluded": 1,
        "unknown": 1,
    }
    assert result["authoritative_decisions_required"] == 1
    assert "@" not in (first.parent / "pii-review.md").read_text("utf-8")


@pytest.mark.parametrize(
    ("pii_type", "action"),
    (
        ("phone", "redact"),
        ("email", "redact"),
        ("api_key", "exclude"),
        ("bot_token", "exclude"),
        ("telegram_username", "replace_with_alias"),
        ("numeric_id", "replace_with_alias"),
        ("public_url", "keep"),
        ("unknown_kind", "manual_review"),
    ),
)
def test_recommended_pii_actions(pii_type: str, action: str) -> None:
    assert recommended_pii_action(pii_type) == action


def test_pilot_is_authoritative_deterministic_diverse_and_verbatim(
    tmp_path: Path,
) -> None:
    episodes = [_episode(index) for index in range(1, 55)]
    episodes.extend(
        [
            _episode(55, classification="human_edited_ai"),
            _episode(80, classification="ai_generated"),
            _episode(81, classification="unknown_historical"),
            _episode(82, classification="conflicting_evidence"),
        ]
    )
    pii = [
        {
            "record_id": "human-pii",
            "pii_type": "phone",
            "episode_ids": ["example-001"],
        }
    ]
    reconciliation, _ = _reconciliation(tmp_path, episodes, pii)
    first = tmp_path / "pilot-one"
    second = tmp_path / "pilot-two"
    result = select_authoritative_pilot(
        reconciliation=reconciliation,
        output=first,
        authoritative_only=True,
        min_examples=50,
        max_examples=52,
    )
    select_authoritative_pilot(
        reconciliation=reconciliation,
        output=second,
        authoritative_only=True,
        min_examples=50,
        max_examples=52,
    )
    selected_text = (first / "selected.preview.jsonl").read_text("utf-8")
    assert selected_text == (second / "selected.preview.jsonl").read_text("utf-8")
    assert result["pilot_selected"] == 52
    assert result["excluded_ai"] == 1
    assert result["excluded_unknown"] == 1
    assert "ai_generated" not in selected_text
    assert "unknown_historical" not in selected_text
    assert "опчтка БЛЯ" in selected_text
    rows = [json.loads(line) for line in selected_text.splitlines() if line.strip()]
    assert {item["stage3c"]["classification"] for item in rows} == {
        "human_confirmed",
        "human_edited_ai",
    }
    assert min(len(item["human_target"]["messages"]) for item in rows) == 1
    assert max(len(item["human_target"]["messages"]) for item in rows) == 2
    assert selection_fingerprint(first) == result["selection_fingerprint"]
    assert all(
        item["incoming"]["messages"] != item["human_target"]["messages"]
        for item in rows
    )


def test_pilot_privacy_scan_blocks_unindexed_pii(tmp_path: Path) -> None:
    episodes = [_episode(index) for index in range(1, 51)]
    episodes[0]["human_target"]["messages"] = ["call +1 202 555 0173"]
    reconciliation, _ = _reconciliation(tmp_path, episodes)
    result = select_authoritative_pilot(
        reconciliation=reconciliation,
        output=tmp_path / "pilot",
        authoritative_only=True,
        min_examples=50,
        max_examples=50,
    )
    assert result["status"] == "PRIVACY_BLOCKED"
    assert result["pilot_selected"] == 0
    assert result["privacy_scan_findings"] == 1


def test_authoritative_confirmation_skips_only_unresolved_affected_example(
    tmp_path: Path,
) -> None:
    episodes = [_episode(index) for index in range(1, 52)]
    episodes.extend(
        [
            _episode(80, classification="ai_generated"),
            _episode(81, classification="unknown_historical"),
        ]
    )
    pii = [
        {
            "record_id": "human-phone",
            "pii_type": "phone",
            "episode_ids": ["example-001"],
        },
        {
            "record_id": "ai-token",
            "pii_type": "api_key",
            "episode_ids": ["example-080"],
        },
        {
            "record_id": "unknown-email",
            "pii_type": "email",
            "episode_ids": ["example-081"],
        },
    ]
    reconciliation, reconciliation_fp = _reconciliation(tmp_path, episodes, pii)
    pilot = tmp_path / "pilot"
    fingerprint = _manual_selection(pilot, episodes[:51], reconciliation_fp)
    decisions = tmp_path / "pii-decisions.csv"
    _write_csv(
        decisions,
        (
            "record_id",
            "pii_type",
            "recommended_action",
            "approved_action",
            "notes",
        ),
        [
            {
                "record_id": "human-phone",
                "pii_type": "phone",
                "recommended_action": "redact",
                "approved_action": "",
                "notes": "",
            }
        ],
    )
    kwargs = {
        "preview": _preview(tmp_path),
        "reconciliation": reconciliation,
        "batch_decisions": None,
        "pilot_selection": pilot,
        "pii_decisions": decisions,
        "fingerprint": fingerprint,
        "authoritative_only": True,
        "max_examples": 51,
        "dataset_root": tmp_path / "dataset",
    }
    with pytest.raises(TelegramCurationError, match="consent"):
        confirm_curated_dataset(**kwargs, consent_confirmed=False)
    bad = {**kwargs, "fingerprint": "0" * 64}
    with pytest.raises(TelegramCurationError, match="fingerprint"):
        confirm_curated_dataset(**bad, consent_confirmed=True)
    result = confirm_curated_dataset(**kwargs, consent_confirmed=True)
    assert result["examples"] == 50
    assert result["unresolved_pii_examples_skipped"] == 1
    payload = (Path(result["dataset"]) / "examples.jsonl").read_text("utf-8")
    assert "example-001" not in payload
    assert "example-080" not in payload
    assert "example-081" not in payload
    assert '"source_type": "imported_human_verified"' in payload
    assert '"authoritative": true' in payload


def test_profiles_use_only_verified_imports_and_resolve_without_rules(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "dataset"
    dataset.mkdir()
    rows = [
        {
            "example_id": "verified",
            "source_type": "imported_human_verified",
            "human_target_bubbles": ["ответ", "ещё"],
            "conversation_context": [],
            "timestamp": BASE_TIME.isoformat(),
            "provenance": {
                "classification": "human_confirmed",
                "verified": True,
            },
        },
        {
            "example_id": "unverified",
            "source_type": "imported_human_verified",
            "human_target_bubbles": ["must not count"],
            "conversation_context": [],
            "timestamp": BASE_TIME.isoformat(),
            "provenance": {
                "classification": "human_confirmed",
                "verified": False,
            },
        },
        {
            "example_id": "manual",
            "source_type": "human_manual",
            "human_target_bubbles": ["must not count"],
            "conversation_context": [],
            "timestamp": BASE_TIME.isoformat(),
            "provenance": {"classification": "human_manual", "verified": True},
        },
    ]
    _write_jsonl(dataset / "examples.jsonl", rows)
    profiles = tmp_path / "profiles"
    result = build_curated_style_profiles(dataset=dataset, output=profiles)
    assert result["eligible_human_examples"] == 1
    assert result["profiles_are_distributions"] is True
    assert result["fixed_rules"] == []
    agent_path = profiles / "agent-style-profile.json"
    relationship_path = profiles / "relationship-style-profile.json"
    agent = json.loads(agent_path.read_text("utf-8"))
    relationship = json.loads(relationship_path.read_text("utf-8"))
    assert agent["profile_type"] != relationship["profile_type"]
    assert "features" in agent
    preview = resolve_profile_preview(
        agent_profile=agent_path,
        relationship_profile=relationship_path,
        limit=30,
    )
    assert preview["fixed_rules"] == []
    assert preview["llm_called"] is False
    assert preview["human_evidence"] == 1


def test_stage3d_keeps_production_openai_only() -> None:
    assert Settings.__dataclass_fields__["generation_mode"].default == "openai_only"
