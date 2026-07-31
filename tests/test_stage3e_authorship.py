from __future__ import annotations

import csv
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import conversation_agent.local_slm.authorship_curation as authorship_module
from conversation_agent.local_slm.authorship_curation import (
    AuthorshipProvenance,
    TransportProvenance,
    apply_conservative_authorship,
    clean_pilot_fingerprint,
    confirm_clean_pilot,
    reconcile_authorship,
    suspicious_authorship_flags,
)
from conversation_agent.local_slm.stage2_dataset import stable_fingerprint
from conversation_agent.local_slm.telegram_curation import (
    TelegramCurationError,
    reconciliation_fingerprint,
)
from conversation_agent.settings import Settings

BASE_TIME = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _episode(index: int, *, bubbles: list[str] | None = None) -> dict[str, Any]:
    timestamp = (BASE_TIME + timedelta(minutes=index)).isoformat()
    target = bubbles or [f"Original HUMAN text {index}"]
    return {
        "example_id": f"example-{index:03d}",
        "agent_id": "fixture-agent",
        "contact_alias": "contact_private_001",
        "relationship_type": "private_contact",
        "context_turns": [],
        "incoming": {
            "role": "contact",
            "messages": [f"incoming {index}"],
            "timestamps": [timestamp],
        },
        "human_target": {
            "role": "human",
            "messages": target,
            "timestamps": [timestamp] * len(target),
            "inter_bubble_delays_seconds": (
                [2.0] * (len(target) - 1) if len(target) > 1 else []
            ),
        },
        "provenance": {"message_ids": [str(1000 + index)]},
        "privacy": {"redactions": []},
        "quality_flags": [],
        "stage3c": {
            "classification": "human_confirmed",
            "authoritative": True,
            "message_record_ids": [f"record-{index:03d}"],
            "heuristic_flags": [],
        },
    }


def _message_result(index: int, evidence_kind: str) -> dict[str, Any]:
    return {
        "record_id": f"record-{index:03d}",
        "telegram_message_id": 1000 + index,
        "classification": "human_confirmed",
        "confidence": 0.94,
        "match_method": "exact_hash_destination_time",
        "evidence_sources": [
            {
                "classification": "human_confirmed",
                "evidence_kind": evidence_kind,
                "metadata": {},
            }
        ],
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            json.dumps(item, ensure_ascii=True, sort_keys=True)
            for item in rows
        )
        + ("\n" if rows else ""),
        encoding="utf-8",
    )


def _reconciliation(
    root: Path,
    episodes: list[dict[str, Any]],
    messages: list[dict[str, Any]],
    *,
    pii: list[dict[str, Any]] | None = None,
) -> tuple[Path, str]:
    path = root / "reconciliation"
    path.mkdir(parents=True)
    _write_json(
        path / "manifest.json",
        {"source_preview_fingerprint": "preview-fixture"},
    )
    _write_jsonl(path / "messages.reconciled.jsonl", messages)
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


def _pilot(
    root: Path,
    episodes: list[dict[str, Any]],
    reconciliation_fp: str,
) -> tuple[Path, str]:
    path = root / "pilot"
    path.mkdir(parents=True)
    selected = [
        {
            **json.loads(json.dumps(item)),
            "source_reconciliation_fingerprint": reconciliation_fp,
        }
        for item in episodes
    ]
    for item in selected:
        item["provenance"].pop("message_ids", None)
        item["stage3c"].pop("message_record_ids", None)
    diversity = {
        "selected": len(selected),
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
    return path, fingerprint


def _preview(root: Path) -> Path:
    path = root / "preview"
    path.mkdir()
    (path / "preview-fingerprint.txt").write_text(
        "preview-fixture\n",
        encoding="utf-8",
    )
    return path


def _fill_decisions(path: Path) -> None:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = [dict(item) for item in csv.DictReader(handle)]
    for row in rows:
        recommendation = row["recommended_action"]
        row["approved_action"] = (
            "include_human"
            if recommendation == "include_human"
            else (
                "exclude_ai"
                if recommendation == "exclude_ai"
                else "exclude_uncertain"
            )
        )
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def _fixture(
    root: Path,
    *,
    count: int = 55,
    pii: list[dict[str, Any]] | None = None,
) -> tuple[Path, Path, list[dict[str, Any]]]:
    episodes = [_episode(index) for index in range(1, count + 1)]
    messages = [
        _message_result(index, "generic_audit:human_manual_input")
        for index in range(1, count + 1)
    ]
    reconciliation, fingerprint = _reconciliation(
        root,
        episodes,
        messages,
        pii=pii,
    )
    pilot, _ = _pilot(root, episodes, fingerprint)
    return reconciliation, pilot, episodes


def test_provenance_entities_keep_transport_and_authorship_separate() -> None:
    transport = TransportProvenance(
        status="audited_send_path",
        authoritative=True,
        evidence=("style_source:human_matvey",),
        confidence=0.94,
    )
    authorship = AuthorshipProvenance(
        status="unknown_authorship",
        authoritative=False,
        evidence=("style_source:human_matvey",),
        confidence=0.0,
    )
    assert transport.authoritative is True
    assert authorship.authoritative is False
    assert transport.to_dict()["status"] != authorship.to_dict()["status"]


@pytest.mark.parametrize(
    ("kind", "expected"),
    (
        ("style_source:human_matvey", "unknown_authorship"),
        ("generated_reply_send_audit", "ai_authored"),
        ("generic_audit:human_manual_input", "human_authored"),
        ("trainer_human_correction", "human_edited_ai"),
    ),
)
def test_authorship_reconciliation_evidence_verdicts(
    tmp_path: Path,
    kind: str,
    expected: str,
) -> None:
    episodes = [_episode(1)]
    reconciliation, fingerprint = _reconciliation(
        tmp_path,
        episodes,
        [_message_result(1, kind)],
    )
    pilot, _ = _pilot(tmp_path, episodes, fingerprint)
    output = tmp_path / "stage3e" / "authorship"
    reconcile_authorship(
        reconciliation=reconciliation,
        pilot_selection=pilot,
        output=output,
    )
    record = json.loads(
        (output / "reconciliation.jsonl").read_text("utf-8").strip()
    )
    assert record["transport"]["authoritative"] is True
    assert record["authorship"]["status"] == expected
    if expected == "unknown_authorship":
        assert record["authorship"]["authoritative"] is False


def test_heuristics_only_prioritize_review_and_cover_required_positions(
    tmp_path: Path,
) -> None:
    reconciliation, pilot, _ = _fixture(tmp_path)
    output = tmp_path / "stage3e" / "authorship"
    result = reconcile_authorship(
        reconciliation=reconciliation,
        pilot_selection=pilot,
        output=output,
    )
    records = [
        json.loads(line)
        for line in (output / "reconciliation.jsonl")
        .read_text("utf-8")
        .splitlines()
    ]
    assert result["human_authored"] == 55
    assert result["heuristics_changed_authorship"] is False
    review_positions = {
        int(item["pilot_position"])
        for item in records
        if "required_stage3e_regression_review" in item["heuristic_flags"]
    }
    assert {7, *range(43, 53)} <= review_positions
    decisions = list(
        csv.DictReader(
            (output.parent / "review" / "authorship-decisions.csv").open(
                "r",
                encoding="utf-8-sig",
                newline="",
            )
        )
    )
    assert len(decisions) == 55
    assert all(not item["approved_action"] for item in decisions)
    review_text = (
        output.parent / "review" / "authorship-review.md"
    ).read_text("utf-8")
    assert review_text.count("\n## ") <= 20


def test_assistant_heuristic_does_not_create_ai_verdict() -> None:
    episode = _episode(
        1,
        bubbles=[
            (
                "\u041c\u043e\u0436\u0435\u043c "
                "\u043f\u043e\u0433\u043e\u0432\u043e\u0440\u0438\u0442\u044c "
                "\u043e\u0431 \u044d\u0442\u043e\u043c?"
            )
        ],
    )
    flags = suspicious_authorship_flags(
        episode,
        position=1,
        duplicate_count=1,
        neighbor_classes=[],
    )
    assert "generic_assistant_response" in flags
    assert "excessive_clarification_question" not in flags


def test_conservative_cleanup_excludes_ai_unknown_and_preserves_text(
    tmp_path: Path,
) -> None:
    episodes = [_episode(index) for index in range(1, 56)]
    episodes[1] = _episode(2, bubbles=["Typoo FUCK", "Second Bubble"])
    messages = [
        _message_result(index, "generic_audit:human_manual_input")
        for index in range(1, 56)
    ]
    messages[52] = _message_result(53, "generated_reply_send_audit")
    messages[53] = _message_result(54, "style_source:human_matvey")
    messages[54] = _message_result(55, "trainer_human_correction")
    reconciliation, recon_fp = _reconciliation(
        tmp_path,
        episodes,
        messages,
    )
    pilot, _ = _pilot(tmp_path, episodes, recon_fp)
    authorship = tmp_path / "stage3e" / "authorship"
    reconcile_authorship(
        reconciliation=reconciliation,
        pilot_selection=pilot,
        output=authorship,
    )
    decisions = authorship.parent / "review" / "authorship-decisions.csv"
    _fill_decisions(decisions)
    clean = tmp_path / "stage3e" / "clean-pilot"
    result = apply_conservative_authorship(
        authorship=authorship,
        decisions=decisions,
        pilot_selection=pilot,
        output=clean,
        exclude_recommended_suspicious=True,
        exclude_unresolved=True,
    )
    assert result["status"] == "CLEAN_PILOT_READY"
    assert result["selected"] == 53
    serialized = (clean / "selected.preview.jsonl").read_text("utf-8")
    assert "example-053" not in serialized
    assert "example-054" not in serialized
    assert "Typoo FUCK" in serialized
    assert "Second Bubble" in serialized
    rows = [json.loads(line) for line in serialized.splitlines()]
    preserved = next(item for item in rows if item["example_id"] == "example-002")
    assert preserved["human_target"]["messages"] == [
        "Typoo FUCK",
        "Second Bubble",
    ]
    assert clean_pilot_fingerprint(clean) == result["clean_pilot_fingerprint"]


def test_clean_confirmation_gates_consent_fingerprint_and_inside_pii(
    tmp_path: Path,
) -> None:
    outside_pii = [
        {
            "record_id": "outside",
            "pii_type": "private_name",
            "episode_ids": ["not-selected"],
        }
    ]
    reconciliation, pilot, _ = _fixture(tmp_path, count=55, pii=outside_pii)
    authorship = tmp_path / "stage3e" / "authorship"
    reconcile_authorship(
        reconciliation=reconciliation,
        pilot_selection=pilot,
        output=authorship,
    )
    decisions = authorship.parent / "review" / "authorship-decisions.csv"
    _fill_decisions(decisions)
    clean = tmp_path / "stage3e" / "clean-pilot"
    cleaned = apply_conservative_authorship(
        authorship=authorship,
        decisions=decisions,
        pilot_selection=pilot,
        output=clean,
        exclude_recommended_suspicious=True,
        exclude_unresolved=True,
    )
    kwargs = {
        "preview": _preview(tmp_path),
        "reconciliation": reconciliation,
        "authorship": authorship,
        "clean_pilot": clean,
        "authorship_decisions": decisions,
        "fingerprint": cleaned["clean_pilot_fingerprint"],
        "dataset_root": tmp_path / "dataset",
    }
    with pytest.raises(TelegramCurationError, match="consent"):
        confirm_clean_pilot(**kwargs, consent_confirmed=False)
    with pytest.raises(TelegramCurationError, match="fingerprint"):
        confirm_clean_pilot(
            **{**kwargs, "fingerprint": "0" * 64},
            consent_confirmed=True,
        )
    confirmed = confirm_clean_pilot(**kwargs, consent_confirmed=True)
    assert confirmed["examples"] == 55
    payload = (Path(confirmed["dataset"]) / "examples.jsonl").read_text(
        "utf-8"
    )
    assert '"source_type": "imported_human_verified"' in payload

    inside_root = tmp_path / "inside"
    inside_pii = [
        {
            "record_id": "inside",
            "pii_type": "private_name",
            "episode_ids": ["example-001"],
        }
    ]
    inside_reconciliation, inside_pilot, _ = _fixture(
        inside_root,
        count=55,
        pii=inside_pii,
    )
    inside_authorship = inside_root / "stage3e" / "authorship"
    reconcile_authorship(
        reconciliation=inside_reconciliation,
        pilot_selection=inside_pilot,
        output=inside_authorship,
    )
    inside_decisions = (
        inside_authorship.parent / "review" / "authorship-decisions.csv"
    )
    _fill_decisions(inside_decisions)
    inside_clean = inside_root / "stage3e" / "clean-pilot"
    inside_result = apply_conservative_authorship(
        authorship=inside_authorship,
        decisions=inside_decisions,
        pilot_selection=inside_pilot,
        output=inside_clean,
        exclude_recommended_suspicious=True,
        exclude_unresolved=True,
    )
    assert inside_result["pii_inside_clean_pilot"] == 0
    assert "example-001" not in (
        inside_clean / "selected.preview.jsonl"
    ).read_text("utf-8")
    clean_rows = [
        json.loads(line)
        for line in (inside_clean / "selected.preview.jsonl")
        .read_text("utf-8")
        .splitlines()
    ]
    source_rows = [
        json.loads(line)
        for line in (inside_pilot / "selected.preview.jsonl")
        .read_text("utf-8")
        .splitlines()
    ]
    record = next(
        json.loads(line)
        for line in (inside_authorship / "reconciliation.jsonl")
        .read_text("utf-8")
        .splitlines()
        if json.loads(line)["example_id"] == "example-001"
    )
    restored = next(
        item for item in source_rows if item["example_id"] == "example-001"
    )
    restored["transport_provenance"] = record["transport"]
    restored["authorship_provenance"] = {
        **record["authorship"],
        "user_approved": True,
    }
    restored["stage3e"] = {
        "source_authorship_fingerprint": inside_result[
            "source_authorship_fingerprint"
        ],
        "source_pilot_fingerprint": inside_result[
            "source_pilot_fingerprint"
        ],
        "pilot_position": 1,
        "heuristic_flags": record["heuristic_flags"],
        "text_normalized": False,
        "bubble_boundaries_preserved": True,
    }
    clean_rows.append(restored)
    exclusions = [
        item
        for item in authorship_module._read_exclusions(
            inside_clean / "exclusions.md"
        )
        if item["example_id"] != "example-001"
    ]
    diversity = authorship_module._clean_diversity(clean_rows)
    forced_fp = stable_fingerprint(
        {
            "source_authorship_fingerprint": inside_result[
                "source_authorship_fingerprint"
            ],
            "source_pilot_fingerprint": inside_result[
                "source_pilot_fingerprint"
            ],
            "selected": clean_rows,
            "exclusions": exclusions,
            "diversity": diversity,
            "conservative": True,
        }
    )
    _write_jsonl(inside_clean / "selected.preview.jsonl", clean_rows)
    authorship_module._write_exclusions(
        inside_clean / "exclusions.md",
        exclusions,
    )
    _write_json(inside_clean / "diversity-report.json", diversity)
    (inside_clean / "fingerprint.txt").write_text(
        forced_fp + "\n",
        encoding="utf-8",
    )
    forced_summary = json.loads(
        (inside_clean / "summary.json").read_text("utf-8")
    )
    forced_summary["clean_pilot_fingerprint"] = forced_fp
    forced_summary["selected"] = len(clean_rows)
    _write_json(inside_clean / "summary.json", forced_summary)
    with pytest.raises(TelegramCurationError, match="PII"):
        confirm_clean_pilot(
            preview=_preview(inside_root),
            reconciliation=inside_reconciliation,
            authorship=inside_authorship,
            clean_pilot=inside_clean,
            authorship_decisions=inside_decisions,
            fingerprint=forced_fp,
            consent_confirmed=True,
            dataset_root=inside_root / "dataset",
        )


def test_clean_pilot_below_minimum_cannot_confirm(tmp_path: Path) -> None:
    reconciliation, pilot, _ = _fixture(tmp_path, count=49)
    authorship = tmp_path / "stage3e" / "authorship"
    reconcile_authorship(
        reconciliation=reconciliation,
        pilot_selection=pilot,
        output=authorship,
    )
    decisions = authorship.parent / "review" / "authorship-decisions.csv"
    _fill_decisions(decisions)
    clean = tmp_path / "stage3e" / "clean-pilot"
    result = apply_conservative_authorship(
        authorship=authorship,
        decisions=decisions,
        pilot_selection=pilot,
        output=clean,
        exclude_recommended_suspicious=True,
        exclude_unresolved=True,
    )
    assert result["status"] == "INSUFFICIENT_AUTHORSHIP_VERIFIED_DATA"
    with pytest.raises(TelegramCurationError, match="at least 50"):
        confirm_clean_pilot(
            preview=_preview(tmp_path),
            reconciliation=reconciliation,
            authorship=authorship,
            clean_pilot=clean,
            authorship_decisions=decisions,
            fingerprint=result["clean_pilot_fingerprint"],
            consent_confirmed=True,
            dataset_root=tmp_path / "dataset",
        )


def test_stage3e_keeps_production_default() -> None:
    assert Settings.__dataclass_fields__["generation_mode"].default == "openai_only"
