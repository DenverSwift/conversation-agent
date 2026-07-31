from __future__ import annotations

import asyncio
import csv
import json
import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from conversation_agent.local_slm.telegram_import import (
    RawTelegramMessage,
    ResolvedEntity,
    TelegramImportError,
    TelegramPreviewOptions,
    TelegramProvenanceResolver,
    build_candidate_episodes,
    build_preview_artifacts,
    confirm_telegram_preview,
    filter_messages_for_preview,
    merge_raw_messages,
    raw_message_from_telethon,
    resolve_numeric_contact,
    segment_turns,
    telegram_review_stats,
    validate_preview,
)
from conversation_agent.local_slm.telegram_privacy import privacy_check, scan_text
from conversation_agent.local_slm.telegram_style_profile import build_style_profiles
from conversation_agent.settings import Settings

CONTACT_FIXTURE_ID = 42424242
OWNER_FIXTURE_ID = 91919191
BASE_TIME = datetime(2024, 1, 1, 12, 0, tzinfo=UTC)


@dataclass
class User:
    id: int
    username: str = "private_fixture"
    first_name: str = "Alice"
    last_name: str = "Example"


class FakeEntityClient:
    def __init__(self, entity: User) -> None:
        self.entity = entity
        self.requested: list[Any] = []

    async def get_entity(self, value: Any) -> User:
        self.requested.append(value)
        return self.entity


def _raw(
    message_id: int,
    *,
    direction: str,
    seconds: int,
    text: str | None = "fixture text",
    caption: str | None = None,
    media_type: str | None = None,
    provenance: str | None = None,
    forwarded: bool = False,
    service: bool = False,
    via_bot: bool = False,
    reply_to: int | None = None,
) -> RawTelegramMessage:
    sender = OWNER_FIXTURE_ID if direction == "outgoing" else CONTACT_FIXTURE_ID
    return RawTelegramMessage(
        message_id=message_id,
        sender_id=sender,
        peer_id=CONTACT_FIXTURE_ID,
        direction=direction,  # type: ignore[arg-type]
        timestamp=(BASE_TIME + timedelta(seconds=seconds)).isoformat(),
        edited_timestamp=None,
        reply_to_message_id=reply_to,
        grouped_media_id=None,
        text=text,
        caption=caption,
        media_type=media_type,
        forwarded=forwarded,
        service_message=service,
        via_bot=via_bot,
        reply_metadata={"reply_to_message_id": reply_to},
        provenance_lookup_status="fixture",
        provenance_classification=provenance,  # type: ignore[arg-type]
        origin_checks=("fixture_provenance",),
    )


def _preview_messages() -> list[RawTelegramMessage]:
    return [
        _raw(1, direction="incoming", seconds=0, text="Hello Alice"),
        _raw(
            2,
            direction="outgoing",
            seconds=10,
            text="first bubble",
            provenance="unknown_historical",
        ),
        _raw(
            3,
            direction="outgoing",
            seconds=20,
            text="second bubble",
            provenance="unknown_historical",
        ),
        _raw(4, direction="incoming", seconds=60, text="Another question?"),
        _raw(
            5,
            direction="outgoing",
            seconds=70,
            text="generated fixture",
            provenance="ai_generated",
        ),
        _raw(
            6,
            direction="outgoing",
            seconds=80,
            text="forwarded fixture",
            provenance="unknown_historical",
            forwarded=True,
        ),
        _raw(7, direction="incoming", seconds=120, text="A caption?"),
        _raw(
            8,
            direction="outgoing",
            seconds=130,
            text=None,
            caption="human caption",
            media_type="photo",
            provenance="human_confirmed",
        ),
    ]


def _build_preview(tmp_path: Path) -> tuple[Path, dict[str, Any]]:
    output = tmp_path / ".runtime" / "private-imports" / "telegram" / "pilot"
    result = build_preview_artifacts(
        messages=_preview_messages(),
        options=TelegramPreviewOptions(
            contact_id=CONTACT_FIXTURE_ID,
            limit=100,
            output=output,
            include_media_metadata=True,
        ),
        resolved=ResolvedEntity(
            entity=User(CONTACT_FIXTURE_ID),
            entity_type="User",
            masked_username="p******e",
            masked_display_name="A**********e",
            resolved_id_suffix="...4242",
            private_names=("Alice", "Example", "private_fixture"),
        ),
        masked_account="O***r",
    )
    return output, result


def test_numeric_contact_resolution_is_strict() -> None:
    client = FakeEntityClient(User(CONTACT_FIXTURE_ID))
    resolved = asyncio.run(resolve_numeric_contact(client, CONTACT_FIXTURE_ID))
    assert client.requested == [CONTACT_FIXTURE_ID]
    assert resolved.resolved_id_suffix == "...4242"

    mismatched = FakeEntityClient(User(CONTACT_FIXTURE_ID + 1))
    with pytest.raises(TelegramImportError, match="does not match"):
        asyncio.run(resolve_numeric_contact(mismatched, CONTACT_FIXTURE_ID))
    with pytest.raises(TelegramImportError, match="positive numeric"):
        asyncio.run(resolve_numeric_contact(client, 0))


def test_contact_id_is_not_hardcoded_in_importer() -> None:
    source = (
        Path(__file__).parents[1]
        / "src"
        / "conversation_agent"
        / "local_slm"
        / "telegram_import.py"
    ).read_text(encoding="utf-8")
    private_id = "175" + "110" + "5897"
    assert private_id not in source


def test_confirm_requires_preview_fingerprint_and_consent(tmp_path: Path) -> None:
    output, result = _build_preview(tmp_path)
    dataset = tmp_path / "datasets" / "private-style"
    with pytest.raises(TelegramImportError, match="consent"):
        confirm_telegram_preview(
            preview=output,
            decisions=None,
            fingerprint=result["fingerprint"],
            consent_confirmed=False,
            dataset_root=dataset,
        )
    with pytest.raises(TelegramImportError, match="fingerprint"):
        confirm_telegram_preview(
            preview=output,
            decisions=None,
            fingerprint="0" * 64,
            consent_confirmed=True,
            dataset_root=dataset,
        )
    with pytest.raises(TelegramImportError, match="existing preview"):
        confirm_telegram_preview(
            preview=tmp_path / "missing",
            decisions=None,
            fingerprint="0" * 64,
            consent_confirmed=True,
            dataset_root=dataset,
        )
    assert not dataset.exists()


def test_turns_bubbles_roles_and_configurable_gap() -> None:
    messages = [
        _raw(1, direction="incoming", seconds=0),
        _raw(
            2,
            direction="outgoing",
            seconds=5,
            text="one",
            provenance="unknown_historical",
        ),
        _raw(
            3,
            direction="outgoing",
            seconds=15,
            text="two",
            provenance="unknown_historical",
        ),
    ]
    turns = segment_turns(messages, turn_gap_seconds=30)
    assert [item.role for item in turns] == ["contact", "human"]
    assert [item.content for item in turns[1].messages] == ["one", "two"]
    episodes, _ = build_candidate_episodes(
        turns,
        context_turns=2,
        max_episodes=10,
    )
    assert episodes[0]["incoming"]["role"] == "contact"
    assert episodes[0]["human_target"]["messages"] == ["one", "two"]
    assert episodes[0]["semantic_plan"] is None

    split_turns = segment_turns(messages, turn_gap_seconds=8)
    assert len(split_turns) == 3


def test_exclusion_rules_and_unknown_review() -> None:
    messages = _preview_messages() + [
        _raw(
            9,
            direction="outgoing",
            seconds=150,
            provenance="unknown_historical",
            via_bot=True,
        ),
        _raw(
            10,
            direction="outgoing",
            seconds=160,
            provenance="unknown_historical",
            service=True,
        ),
        _raw(
            11,
            direction="outgoing",
            seconds=170,
            text=None,
            provenance="unknown_historical",
        ),
    ]
    included, excluded = filter_messages_for_preview(
        messages,
        exclude_forwarded=True,
    )
    reasons = Counter(
        reason for item in excluded for reason in item.get("reasons", [])
    )
    assert reasons["ai_generated"] == 1
    assert reasons["forwarded"] == 1
    assert reasons["via_bot"] == 1
    assert reasons["service_message"] == 1
    assert reasons["empty_or_media_only"] == 1
    assert all(item.direction == "incoming" or item.content for item in included)

    turns = segment_turns(included, turn_gap_seconds=180)
    episodes, _ = build_candidate_episodes(
        turns,
        context_turns=4,
        max_episodes=20,
    )
    unknown = next(
        item
        for item in episodes
        if item["provenance"]["classification"] == "unknown_historical"
    )
    assert unknown["quality_flags"] == ["unknown_provenance_review_required"]
    assert all(item["human_target"]["role"] == "human" for item in episodes)


def test_provenance_repository_excludes_ai_and_detects_human_edit(
    tmp_path: Path,
) -> None:
    database = tmp_path / "feedback.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.execute(
            """
            CREATE TABLE generated_replies (
                dialog_id INTEGER,
                sent_message_id INTEGER,
                created_at TEXT,
                feedback_status TEXT,
                corrected_reply_text TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO generated_replies VALUES (?, ?, ?, ?, ?)",
            (
                CONTACT_FIXTURE_ID,
                22,
                BASE_TIME.isoformat(),
                "corrected",
                "human correction",
            ),
        )
    resolver = TelegramProvenanceResolver(database, dialog_id=CONTACT_FIXTURE_ID)
    ai = resolver.classify(
        _raw(22, direction="outgoing", seconds=5, provenance=None)
    )
    edited = resolver.classify(
        _raw(
            23,
            direction="outgoing",
            seconds=6,
            text="human correction",
            provenance=None,
        )
    )
    unknown = resolver.classify(
        _raw(24, direction="outgoing", seconds=7, provenance=None)
    )
    assert ai.provenance_classification == "ai_generated"
    assert edited.provenance_classification == "human_edited_ai"
    assert unknown.provenance_classification == "unknown_historical"


def test_media_metadata_and_caption_do_not_download() -> None:
    class Photo:
        pass

    message = SimpleNamespace(
        id=1,
        sender_id=OWNER_FIXTURE_ID,
        peer_id=SimpleNamespace(user_id=CONTACT_FIXTURE_ID),
        out=True,
        date=BASE_TIME,
        edit_date=None,
        reply_to=None,
        reply_to_msg_id=None,
        grouped_id=123,
        message="caption fixture",
        text="caption fixture",
        media=Photo(),
        fwd_from=None,
        action=None,
        via_bot_id=None,
    )
    converted = raw_message_from_telethon(
        message,
        own_id=OWNER_FIXTURE_ID,
        contact_id=CONTACT_FIXTURE_ID,
        include_media_metadata=True,
    )
    assert converted.media_type == "photo"
    assert converted.caption == "caption fixture"
    assert converted.text is None
    assert not hasattr(message, "download_media")


def test_pii_scanner_detects_required_local_patterns() -> None:
    text = (
        "mail me at person@example.test or +1 202 555 0199; "
        "card 4242 4242 4242 4242; api_key=sk-abcdefghijklmnopqr"
    )
    kinds = {item.kind for item in scan_text(text)}
    assert {"email", "phone", "payment_card", "api_key"} <= kinds


def test_preview_is_pseudonymized_private_and_complete(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    output, result = _build_preview(tmp_path)
    assert result["confirmed"] is False
    assert validate_preview(output)["valid"] is True
    assert privacy_check(output)["valid"] is True
    assert {item.name for item in output.iterdir()} >= {
        "manifest.json",
        "summary.md",
        "episodes.preview.jsonl",
        "review-sample.md",
        "style-profile-preview.json",
        "relationship-profile-preview.json",
        "excluded.jsonl",
        "privacy-report.json",
        "review-decisions.csv",
        "preview-fingerprint.txt",
    }
    preview_text = (output / "episodes.preview.jsonl").read_text(encoding="utf-8")
    assert "Hello Alice" not in preview_text
    assert "[redacted:private_name]" in preview_text
    assert "Hello Alice" not in caplog.text
    assert ".runtime" in str(output)
    assert "benchmark" not in json.loads(
        (output / "manifest.json").read_text(encoding="utf-8")
    )["import_type"]


def test_profiles_use_only_human_candidates_and_are_adaptive(tmp_path: Path) -> None:
    output, _ = _build_preview(tmp_path)
    episodes = [
        json.loads(line)
        for line in (output / "episodes.preview.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line
    ]
    episodes.append(
        {
            "human_target": {"messages": ["AI SHOULD NOT COUNT"], "timestamps": []},
            "incoming": {"messages": ["context"], "timestamps": []},
            "context_turns": [],
            "provenance": {"classification": "ai_generated"},
        }
    )
    agent, relationship = build_style_profiles(
        episodes,
        agent_id="fixture-agent",
        relationship_id="fixture-relationship",
        generated_at=BASE_TIME.isoformat(),
    )
    assert agent["sample_count"] == len(episodes) - 1
    assert agent["profile_type"] == "agent_style_preview"
    assert relationship["profile_type"] == "relationship_style_preview"
    assert relationship["relationship_id"] != agent.get("relationship_id")
    assert agent["fixed_rules"] == []
    assert agent["interpretation"] == "descriptive_distributions_not_prescriptive_rules"
    assert "bubble_count" in agent["features"]
    assert "casing" in agent["features"]
    assert "AI SHOULD NOT COUNT" not in json.dumps(agent)


def test_empty_review_is_not_approval(tmp_path: Path) -> None:
    output, _ = _build_preview(tmp_path)
    stats = telegram_review_stats(output)
    assert stats["approved"] == 0
    assert stats["pending"] == stats["total"]
    assert stats["empty_is_approval"] is False

    decisions = output / "review-decisions.csv"
    with decisions.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0]["include"] = "yes"
    rows[0]["privacy_ok"] = "yes"
    rows[0]["provenance_ok"] = "yes"
    with decisions.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    assert telegram_review_stats(output)["approved"] == 1


def test_confirmed_payload_uses_aliases_and_removes_telegram_ids(
    tmp_path: Path,
) -> None:
    output, result = _build_preview(tmp_path)
    decisions = output / "review-decisions.csv"
    with decisions.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    rows[0].update(
        {
            "include": "yes",
            "privacy_ok": "yes",
            "provenance_ok": "yes",
        }
    )
    with decisions.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)
    dataset = tmp_path / "datasets" / "private-style"
    confirmed = confirm_telegram_preview(
        preview=output,
        decisions=decisions,
        fingerprint=result["fingerprint"],
        consent_confirmed=True,
        dataset_root=dataset,
    )
    assert confirmed["examples"] == 1
    payload = json.loads(next((dataset / "raw").glob("*.json")).read_text("utf-8"))
    serialized = json.dumps(payload)
    assert payload["source_type"] == "imported_human_verified"
    assert payload["semantic_plan"] is None
    assert payload["provenance"]["raw_identifiers_included"] is False
    assert str(CONTACT_FIXTURE_ID) not in serialized
    assert "message_ids" not in serialized


def test_duplicate_episodes_and_resume_are_deterministic() -> None:
    messages = [
        _raw(1, direction="incoming", seconds=0, text="same"),
        _raw(
            2,
            direction="outgoing",
            seconds=10,
            text="answer",
            provenance="unknown_historical",
        ),
        _raw(3, direction="incoming", seconds=20, text="same"),
        _raw(
            4,
            direction="outgoing",
            seconds=30,
            text="answer",
            provenance="unknown_historical",
        ),
    ]
    episodes, excluded = build_candidate_episodes(
        segment_turns(messages, turn_gap_seconds=180),
        context_turns=2,
        max_episodes=10,
    )
    assert len(episodes) == 1
    assert excluded[0]["reasons"] == ["duplicate_episode"]

    resumed = merge_raw_messages(messages[:2], messages, limit=10)
    assert [item.message_id for item in resumed] == [1, 2, 3, 4]


def test_private_paths_are_gitignored_and_production_default_is_unchanged() -> None:
    gitignore = (Path(__file__).parents[1] / ".gitignore").read_text(encoding="utf-8")
    assert ".runtime/" in gitignore
    assert "datasets/private-style/raw/*" in gitignore
    assert Settings.__dataclass_fields__["generation_mode"].default == "openai_only"
