"""Read-only, local-only Telegram history preview and confirmation gates."""

from __future__ import annotations

import asyncio
import csv
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter
from collections.abc import AsyncIterator, Iterable
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from conversation_agent.local_slm.stage2_dataset import stable_fingerprint
from conversation_agent.local_slm.telegram_privacy import (
    privacy_check,
    redact_text,
    scan_text,
)
from conversation_agent.local_slm.telegram_style_profile import build_style_profiles
from conversation_agent.settings import load_env_file

ProvenanceClass = Literal[
    "human_confirmed",
    "ai_generated",
    "human_edited_ai",
    "unknown_historical",
]

REQUIRED_PREVIEW_FILES = frozenset(
    {
        "manifest.json",
        "summary.md",
        "episodes.preview.jsonl",
        "review-sample.md",
        "style-profile-preview.json",
        "relationship-profile-preview.json",
        "exclusions-summary.json",
        "excluded.jsonl",
        "privacy-report.json",
        "provenance-report.json",
        "review-decisions.csv",
        "preview-fingerprint.txt",
        "raw-messages.private.jsonl",
    }
)
SEVERE_PRIVACY_KINDS = frozenset(
    {
        "api_key",
        "bank_details",
        "bot_token",
        "password",
        "payment_card",
        "session_string",
        "secret_url",
    }
)
DEFAULT_CONTEXT_TURNS = 6
DEFAULT_TURN_GAP_SECONDS = 180
DEFAULT_MAX_EPISODES = 200
DEFAULT_REVIEW_SAMPLE_SIZE = 75


class TelegramImportError(ValueError):
    """Raised when a private Telegram import safety gate fails."""


@dataclass(frozen=True)
class RawTelegramMessage:
    message_id: int
    sender_id: int | None
    peer_id: int | None
    direction: Literal["incoming", "outgoing", "unknown"]
    timestamp: str
    edited_timestamp: str | None
    reply_to_message_id: int | None
    grouped_media_id: int | None
    text: str | None
    caption: str | None
    media_type: str | None
    forwarded: bool
    service_message: bool
    via_bot: bool
    reply_metadata: dict[str, Any]
    provenance_lookup_status: str = "pending"
    provenance_classification: ProvenanceClass | None = None
    origin_checks: tuple[str, ...] = ()

    @property
    def content(self) -> str:
        return (self.caption if self.media_type else self.text) or ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["origin_checks"] = list(self.origin_checks)
        value["media"] = (
            {
                "type": self.media_type,
                "has_caption": bool(self.caption),
                "caption": self.caption,
            }
            if self.media_type
            else None
        )
        return value

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> RawTelegramMessage:
        direction = str(value.get("direction", "unknown"))
        if direction not in {"incoming", "outgoing", "unknown"}:
            direction = "unknown"
        provenance = value.get("provenance_classification")
        if provenance not in {
            "human_confirmed",
            "ai_generated",
            "human_edited_ai",
            "unknown_historical",
            None,
        }:
            provenance = None
        return cls(
            message_id=int(value["message_id"]),
            sender_id=_optional_int(value.get("sender_id")),
            peer_id=_optional_int(value.get("peer_id")),
            direction=direction,  # type: ignore[arg-type]
            timestamp=str(value.get("timestamp", "")),
            edited_timestamp=_optional_str(value.get("edited_timestamp")),
            reply_to_message_id=_optional_int(value.get("reply_to_message_id")),
            grouped_media_id=_optional_int(value.get("grouped_media_id")),
            text=_optional_str(value.get("text")),
            caption=_optional_str(value.get("caption")),
            media_type=_optional_str(value.get("media_type")),
            forwarded=bool(value.get("forwarded")),
            service_message=bool(value.get("service_message")),
            via_bot=bool(value.get("via_bot")),
            reply_metadata=dict(value.get("reply_metadata", {})),
            provenance_lookup_status=str(
                value.get("provenance_lookup_status", "pending")
            ),
            provenance_classification=provenance,  # type: ignore[arg-type]
            origin_checks=tuple(str(item) for item in value.get("origin_checks", [])),
        )


@dataclass(frozen=True)
class MessageTurn:
    role: Literal["human", "contact"]
    messages: tuple[RawTelegramMessage, ...]

    @property
    def first_timestamp(self) -> str:
        return self.messages[0].timestamp

    @property
    def last_timestamp(self) -> str:
        return self.messages[-1].timestamp


@dataclass(frozen=True)
class ResolvedEntity:
    entity: Any
    entity_type: str
    masked_username: str
    masked_display_name: str
    resolved_id_suffix: str
    private_names: tuple[str, ...] = field(default=(), repr=False)

    def safe_dict(self) -> dict[str, str]:
        return {
            "entity_type": self.entity_type,
            "masked_username": self.masked_username,
            "masked_display_name": self.masked_display_name,
            "resolved_id_suffix": self.resolved_id_suffix,
        }


@dataclass(frozen=True)
class TelegramPreviewOptions:
    contact_id: int
    limit: int
    output: Path
    since: datetime | None = None
    until: datetime | None = None
    session: str | None = None
    account_id: int | None = None
    context_turns: int = DEFAULT_CONTEXT_TURNS
    turn_gap_seconds: int = DEFAULT_TURN_GAP_SECONDS
    include_media_metadata: bool = False
    exclude_forwarded: bool = True
    resume: bool = False
    max_episodes: int = DEFAULT_MAX_EPISODES
    review_sample_size: int = DEFAULT_REVIEW_SAMPLE_SIZE
    agent_id: str = "private-agent"
    relationship_type: str = "private_contact"


@dataclass(frozen=True)
class TelegramImportEnvironment:
    api_id: int
    api_hash: str
    session_path: str
    feedback_database_path: Path


class TelegramProvenanceResolver:
    """Resolve outgoing origins from local send/feedback audit state only."""

    def __init__(self, database_path: Path, *, dialog_id: int) -> None:
        self.database_path = database_path
        self.dialog_id = dialog_id
        self._ai_messages: dict[int, dict[str, Any]] = {}
        self._corrected_text_hashes: set[str] = set()
        self._earliest_generation: datetime | None = None
        self._load()

    def classify(self, message: RawTelegramMessage) -> RawTelegramMessage:
        if message.direction != "outgoing":
            return replace(
                message,
                provenance_lookup_status="context_only",
                provenance_classification=None,
                origin_checks=("incoming_context_only",),
            )
        checks = ["feedback_repository_checked", "send_audit_checked"]
        if message.message_id in self._ai_messages:
            checks.append("sent_message_id_matches_generated_reply")
            return replace(
                message,
                provenance_lookup_status="matched",
                provenance_classification="ai_generated",
                origin_checks=tuple(checks),
            )
        content_hash = _text_hash(message.content)
        if content_hash and content_hash in self._corrected_text_hashes:
            checks.append("text_matches_recorded_human_correction")
            return replace(
                message,
                provenance_lookup_status="matched",
                provenance_classification="human_edited_ai",
                origin_checks=tuple(checks),
            )
        timestamp = _parse_datetime(message.timestamp)
        if (
            timestamp is not None
            and self._earliest_generation is not None
            and timestamp < self._earliest_generation
        ):
            checks.append("predates_local_generation_records_unverified")
        else:
            checks.append("no_local_origin_mapping")
        return replace(
            message,
            provenance_lookup_status="review_required",
            provenance_classification="unknown_historical",
            origin_checks=tuple(checks),
        )

    def _load(self) -> None:
        if not self.database_path.is_file():
            return
        try:
            with sqlite3.connect(self.database_path) as connection:
                connection.row_factory = sqlite3.Row
                table = connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='generated_replies'"
                ).fetchone()
                if table is None:
                    return
                rows = connection.execute(
                    """
                    SELECT sent_message_id, created_at, feedback_status,
                           corrected_reply_text
                    FROM generated_replies
                    WHERE dialog_id = ?
                    """,
                    (self.dialog_id,),
                ).fetchall()
        except sqlite3.Error:
            return
        for row in rows:
            sent_id = _optional_int(row["sent_message_id"])
            if sent_id is not None:
                self._ai_messages[sent_id] = {
                    "feedback_status": row["feedback_status"],
                }
            corrected = _optional_str(row["corrected_reply_text"])
            if corrected:
                self._corrected_text_hashes.add(_text_hash(corrected))
            created = _parse_datetime(str(row["created_at"]))
            if created is not None and (
                self._earliest_generation is None or created < self._earliest_generation
            ):
                self._earliest_generation = created


async def run_telegram_preview(options: TelegramPreviewOptions) -> dict[str, Any]:
    environment = load_telegram_import_environment(options.session)
    session_file = _session_file(Path(environment.session_path))
    if not session_file.is_file():
        raise TelegramImportError(
            "Telegram session is missing. Run: python -m conversation_agent login"
        )
    from telethon import TelegramClient

    client: Any = TelegramClient(
        environment.session_path,
        environment.api_id,
        environment.api_hash,
    )
    await client.connect()
    try:
        if not await client.is_user_authorized():
            raise TelegramImportError(
                "Telegram session is not authorized. Run: "
                "python -m conversation_agent login"
            )
        own_entity = await client.get_me()
        own_id = int(own_entity.id)
        if options.account_id is not None and own_id != options.account_id:
            raise TelegramImportError("authorized Telegram account does not match --account-id")
        resolved = await resolve_numeric_contact(client, options.contact_id)
        print("Resolved Telegram contact (masked):")
        for key, value in resolved.safe_dict().items():
            print(f"  {key}: {value}")
        raw = [
            item
            async for item in fetch_raw_messages(
                client,
                resolved.entity,
                own_id=own_id,
                contact_id=options.contact_id,
                limit=options.limit,
                since=options.since,
                until=options.until,
                include_media_metadata=options.include_media_metadata,
            )
        ]
        existing = (
            load_raw_messages(options.output / "raw-messages.private.jsonl")
            if options.resume
            else []
        )
        raw = merge_raw_messages(existing, raw, limit=options.limit)
        resolver = TelegramProvenanceResolver(
            environment.feedback_database_path,
            dialog_id=options.contact_id,
        )
        classified = [resolver.classify(item) for item in raw]
        return build_preview_artifacts(
            messages=classified,
            options=options,
            resolved=resolved,
            masked_account=_masked_entity(own_entity),
            account_private_names=_entity_private_names(own_entity),
        )
    finally:
        await client.disconnect()


def load_telegram_import_environment(
    session_override: str | None = None,
    *,
    env_file: Path = Path(".env"),
) -> TelegramImportEnvironment:
    load_env_file(env_file)
    try:
        api_id = int(os.environ["TELEGRAM_API_ID"].strip())
        api_hash = os.environ["TELEGRAM_API_HASH"].strip()
        session_path = session_override or os.environ["TELEGRAM_SESSION_PATH"].strip()
    except (KeyError, ValueError) as exc:
        raise TelegramImportError(
            "TELEGRAM_API_ID, TELEGRAM_API_HASH and TELEGRAM_SESSION_PATH are required"
        ) from exc
    if not api_hash or not session_path:
        raise TelegramImportError("Telegram import settings must not be empty")
    return TelegramImportEnvironment(
        api_id=api_id,
        api_hash=api_hash,
        session_path=session_path,
        feedback_database_path=Path(
            os.environ.get("FEEDBACK_DATABASE_PATH", ".runtime/feedback.sqlite3")
        ),
    )


async def resolve_numeric_contact(client: Any, contact_id: int) -> ResolvedEntity:
    if not isinstance(contact_id, int) or contact_id <= 0:
        raise TelegramImportError("contact ID must be a positive numeric Telegram user ID")
    entity = await client.get_entity(contact_id)
    resolved_id = int(getattr(entity, "id", 0))
    if resolved_id != contact_id:
        raise TelegramImportError("resolved Telegram entity ID does not match requested ID")
    entity_type = type(entity).__name__
    if entity_type.casefold() != "user":
        raise TelegramImportError("resolved Telegram entity is not a user")
    username = str(getattr(entity, "username", "") or "")
    first_name = str(getattr(entity, "first_name", "") or "")
    last_name = str(getattr(entity, "last_name", "") or "")
    display_name = " ".join(item for item in (first_name, last_name) if item)
    return ResolvedEntity(
        entity=entity,
        entity_type=entity_type,
        masked_username=_mask_value(username),
        masked_display_name=_mask_value(display_name),
        resolved_id_suffix=f"...{str(resolved_id)[-4:]}",
        private_names=tuple(
            item for item in (username, first_name, last_name, display_name) if item
        ),
    )


async def fetch_raw_messages(
    client: Any,
    entity: Any,
    *,
    own_id: int,
    contact_id: int,
    limit: int,
    since: datetime | None,
    until: datetime | None,
    include_media_metadata: bool,
) -> AsyncIterator[RawTelegramMessage]:
    if limit <= 0:
        raise TelegramImportError("--limit must be greater than zero")
    rows: list[RawTelegramMessage] = []
    async for message in client.iter_messages(entity, limit=limit):
        converted = raw_message_from_telethon(
            message,
            own_id=own_id,
            contact_id=contact_id,
            include_media_metadata=include_media_metadata,
        )
        timestamp = _parse_datetime(converted.timestamp)
        if since is not None and timestamp is not None and timestamp < since:
            continue
        if until is not None and timestamp is not None and timestamp > until:
            continue
        rows.append(converted)
    for row in reversed(rows):
        yield row


def raw_message_from_telethon(
    message: Any,
    *,
    own_id: int,
    contact_id: int,
    include_media_metadata: bool,
) -> RawTelegramMessage:
    sender_id = _optional_int(getattr(message, "sender_id", None))
    outgoing = bool(getattr(message, "out", False))
    if outgoing and sender_id in {None, own_id}:
        direction: Literal["incoming", "outgoing", "unknown"] = "outgoing"
    elif not outgoing and sender_id == contact_id:
        direction = "incoming"
    else:
        direction = "unknown"
    media_type = _telegram_media_type(message) if include_media_metadata else None
    raw_text = str(
        getattr(message, "message", None)
        or getattr(message, "text", None)
        or ""
    )
    caption = raw_text if media_type and raw_text else None
    text = None if media_type else (raw_text or None)
    reply_to = getattr(message, "reply_to", None)
    reply_to_id = _optional_int(
        getattr(message, "reply_to_msg_id", None)
        or getattr(reply_to, "reply_to_msg_id", None)
    )
    peer_id = _extract_peer_id(getattr(message, "peer_id", None))
    timestamp = _isoformat(getattr(message, "date", None))
    edited = getattr(message, "edit_date", None)
    return RawTelegramMessage(
        message_id=int(message.id),
        sender_id=sender_id,
        peer_id=peer_id,
        direction=direction,
        timestamp=timestamp,
        edited_timestamp=_isoformat(edited) if edited else None,
        reply_to_message_id=reply_to_id,
        grouped_media_id=_optional_int(getattr(message, "grouped_id", None)),
        text=text,
        caption=caption,
        media_type=media_type,
        forwarded=getattr(message, "fwd_from", None) is not None,
        service_message=(
            getattr(message, "action", None) is not None
            or type(message).__name__.casefold() == "messageservice"
        ),
        via_bot=_optional_int(getattr(message, "via_bot_id", None)) is not None,
        reply_metadata={
            "reply_to_message_id": reply_to_id,
            "reply_to_top_id": _optional_int(getattr(reply_to, "reply_to_top_id", None)),
            "forum_topic": bool(getattr(reply_to, "forum_topic", False)),
        },
    )


def merge_raw_messages(
    existing: Iterable[RawTelegramMessage],
    fetched: Iterable[RawTelegramMessage],
    *,
    limit: int,
) -> list[RawTelegramMessage]:
    merged = {item.message_id: item for item in existing}
    merged.update({item.message_id: item for item in fetched})
    ordered = sorted(merged.values(), key=lambda item: (item.timestamp, item.message_id))
    return ordered[-limit:]


def build_preview_artifacts(
    *,
    messages: list[RawTelegramMessage],
    options: TelegramPreviewOptions,
    resolved: ResolvedEntity,
    masked_account: str,
    account_private_names: Iterable[str] = (),
) -> dict[str, Any]:
    output = options.output
    output.mkdir(parents=True, exist_ok=True)
    private_names = tuple(dict.fromkeys((*resolved.private_names, *account_private_names)))
    included, excluded = filter_messages_for_preview(
        messages,
        exclude_forwarded=options.exclude_forwarded,
        private_names=private_names,
    )
    turns = segment_turns(included, turn_gap_seconds=options.turn_gap_seconds)
    episodes, episode_exclusions = build_candidate_episodes(
        turns,
        context_turns=options.context_turns,
        max_episodes=options.max_episodes,
        private_names=private_names,
    )
    excluded.extend(episode_exclusions)
    generated_at = datetime.now(UTC).isoformat()
    agent_profile, relationship_profile = build_style_profiles(
        episodes,
        agent_id=options.agent_id,
        relationship_id=options.relationship_type,
        generated_at=generated_at,
    )
    privacy_findings = [
        finding
        for message in messages
        for finding in scan_text(message.content, private_names=private_names)
    ]
    provenance_counts = Counter(
        item.provenance_classification
        for item in messages
        if item.direction == "outgoing" and item.provenance_classification
    )
    exclusion_counts = Counter(
        reason
        for item in excluded
        for reason in item.get("reasons", [])
    )
    manifest: dict[str, Any] = {
        "schema_version": 1,
        "import_type": "private_telegram_preview",
        "created_at": generated_at,
        "local_only": True,
        "read_only": True,
        "confirmed_import": False,
        "training_performed": False,
        "external_services_called": [],
        "contact": resolved.safe_dict(),
        "account": masked_account,
        "contact_alias": "contact_private_001",
        "agent_id": options.agent_id,
        "relationship_type": options.relationship_type,
        "requested_limit": options.limit,
        "max_candidate_episodes": options.max_episodes,
        "review_sample_size": options.review_sample_size,
        "context_turns": options.context_turns,
        "turn_gap_seconds": options.turn_gap_seconds,
        "exclude_forwarded": options.exclude_forwarded,
        "include_media_metadata": options.include_media_metadata,
        "resume": options.resume,
        "since": options.since.isoformat() if options.since else None,
        "until": options.until.isoformat() if options.until else None,
        "fetched_messages": len(messages),
        "date_range": {
            "from": min((item.timestamp for item in messages), default=None),
            "until": max((item.timestamp for item in messages), default=None),
        },
        "incoming_count": sum(item.direction == "incoming" for item in messages),
        "outgoing_count": sum(item.direction == "outgoing" for item in messages),
        "built_turns": len(turns),
        "candidate_episodes": len(episodes),
        "excluded_records": len(excluded),
        "duplicate_count": exclusion_counts.get("duplicate_episode", 0),
        "media_metadata_count": sum(bool(item.media_type) for item in messages),
        "privacy_finding_count": len(privacy_findings),
        "provenance_counts": dict(sorted(provenance_counts.items())),
        "production_generation_mode_changed": False,
        "semantic_enrichment_status": "pending",
        "preview_fingerprint": None,
    }
    privacy_report = {
        "schema_version": 1,
        "local_only": True,
        "raw_logs_contain_text": False,
        "preview_pseudonymized": True,
        "external_services_called": [],
        "findings": len(privacy_findings),
        "finding_types": dict(
            sorted(Counter(item.kind for item in privacy_findings).items())
        ),
        "severe_findings_excluded_from_targets": True,
        "contact_identifier_in_training_payload": False,
    }
    provenance_report = {
        "schema_version": 1,
        "outgoing_messages": sum(item.direction == "outgoing" for item in messages),
        "classifications": dict(sorted(provenance_counts.items())),
        "human_target_policy": {
            "human_confirmed": "candidate",
            "human_edited_ai": "candidate_final_human_text_only",
            "ai_generated": "excluded",
            "unknown_historical": "candidate_requires_batch_review",
        },
        "incoming_messages_are_style_evidence": False,
        "ai_output_is_style_evidence": False,
    }
    exclusions_summary = {
        "excluded_records": len(excluded),
        "reasons": dict(sorted(exclusion_counts.items())),
    }
    _write_jsonl(output / "raw-messages.private.jsonl", [item.to_dict() for item in messages])
    _write_jsonl(output / "episodes.preview.jsonl", episodes)
    _write_jsonl(output / "excluded.jsonl", excluded)
    _write_json(output / "manifest.json", manifest)
    _write_json(output / "style-profile-preview.json", agent_profile)
    _write_json(
        output / "relationship-profile-preview.json",
        relationship_profile,
    )
    _write_json(output / "privacy-report.json", privacy_report)
    _write_json(output / "provenance-report.json", provenance_report)
    _write_json(output / "exclusions-summary.json", exclusions_summary)
    _write_review_decisions(output / "review-decisions.csv", episodes)
    fingerprint = compute_preview_fingerprint(output)
    manifest["preview_fingerprint"] = fingerprint
    _write_json(output / "manifest.json", manifest)
    (output / "preview-fingerprint.txt").write_text(fingerprint + "\n", encoding="utf-8")
    (output / "summary.md").write_text(
        _summary_markdown(manifest, output, fingerprint),
        encoding="utf-8",
    )
    sample = select_review_sample(episodes, limit=options.review_sample_size)
    (output / "review-sample.md").write_text(
        _review_sample_markdown(sample),
        encoding="utf-8",
    )
    validation = validate_preview(output, expected_fingerprint=fingerprint)
    if not validation["valid"]:
        raise TelegramImportError(
            "generated preview failed validation: " + ", ".join(validation["errors"])
        )
    return {
        "preview": str(output),
        "fingerprint": fingerprint,
        "fetched_messages": len(messages),
        "candidate_episodes": len(episodes),
        "excluded_records": len(excluded),
        "privacy_findings": len(privacy_findings),
        "confirmed": False,
        "next_command": _confirmation_command(output, fingerprint),
    }


def filter_messages_for_preview(
    messages: Iterable[RawTelegramMessage],
    *,
    exclude_forwarded: bool,
    private_names: Iterable[str] = (),
) -> tuple[list[RawTelegramMessage], list[dict[str, Any]]]:
    included: list[RawTelegramMessage] = []
    excluded: list[dict[str, Any]] = []
    break_before_next = False
    for message in messages:
        reasons: list[str] = []
        content = message.content
        findings = scan_text(content, private_names=private_names)
        if message.direction == "unknown":
            reasons.append("unknown_direction")
        if message.service_message:
            reasons.append("service_message")
        if message.via_bot:
            reasons.append("via_bot")
        if message.forwarded and exclude_forwarded:
            reasons.append("forwarded")
        if not content.strip():
            reasons.append("empty_or_media_only")
        if message.direction == "outgoing" and content.lstrip().startswith("/"):
            reasons.append("bot_command")
        if (
            message.direction == "outgoing"
            and message.provenance_classification == "ai_generated"
        ):
            reasons.append("ai_generated")
        if message.direction == "outgoing" and any(
            item.kind in SEVERE_PRIVACY_KINDS for item in findings
        ):
            reasons.append("sensitive_payload")
        if reasons:
            excluded.append(
                {
                    "record_id": _private_record_id(message.message_id),
                    "direction": message.direction,
                    "timestamp": message.timestamp,
                    "reasons": sorted(set(reasons)),
                    "media_type": message.media_type,
                    "provenance": message.provenance_classification,
                }
            )
            if message.service_message:
                break_before_next = True
            continue
        if break_before_next:
            message = replace(
                message,
                reply_metadata={
                    **message.reply_metadata,
                    "_segment_break_before": True,
                },
            )
            break_before_next = False
        included.append(message)
    return included, excluded


def segment_turns(
    messages: Iterable[RawTelegramMessage],
    *,
    turn_gap_seconds: int,
) -> list[MessageTurn]:
    if turn_gap_seconds <= 0:
        raise TelegramImportError("--turn-gap-seconds must be greater than zero")
    turns: list[MessageTurn] = []
    current: list[RawTelegramMessage] = []
    current_role: Literal["human", "contact"] | None = None
    for message in sorted(messages, key=lambda item: (item.timestamp, item.message_id)):
        role: Literal["human", "contact"] = (
            "human" if message.direction == "outgoing" else "contact"
        )
        starts_new = current_role is not None and role != current_role
        if current:
            previous = current[-1]
            gap = _seconds_between(previous.timestamp, message.timestamp)
            starts_new = (
                starts_new
                or gap > turn_gap_seconds
                or message.service_message
                or bool(message.reply_metadata.get("_segment_break_before"))
                or (
                    message.reply_to_message_id is not None
                    and message.reply_to_message_id
                    not in {item.message_id for item in current}
                )
            )
        if starts_new and current_role is not None:
            turns.append(MessageTurn(role=current_role, messages=tuple(current)))
            current = []
        current.append(message)
        current_role = role
    if current and current_role is not None:
        turns.append(MessageTurn(role=current_role, messages=tuple(current)))
    return turns


def build_candidate_episodes(
    turns: list[MessageTurn],
    *,
    context_turns: int,
    max_episodes: int,
    private_names: Iterable[str] = (),
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if context_turns < 1:
        raise TelegramImportError("--context-turns must be greater than zero")
    if max_episodes < 1:
        raise TelegramImportError("--max-episodes must be greater than zero")
    episodes: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, turn in enumerate(turns):
        if turn.role != "human":
            continue
        if index == 0 or turns[index - 1].role != "contact":
            excluded.append(
                {
                    "record_id": _private_record_id(turn.messages[0].message_id),
                    "direction": "outgoing",
                    "timestamp": turn.first_timestamp,
                    "reasons": ["missing_incoming_contact_turn"],
                }
            )
            continue
        incoming_turn = turns[index - 1]
        provenance_values = {
            item.provenance_classification or "unknown_historical"
            for item in turn.messages
        }
        if "ai_generated" in provenance_values:
            excluded.append(
                {
                    "record_id": _private_record_id(turn.messages[0].message_id),
                    "direction": "outgoing",
                    "timestamp": turn.first_timestamp,
                    "reasons": ["ai_generated"],
                }
            )
            continue
        classification: ProvenanceClass = (
            "human_edited_ai"
            if "human_edited_ai" in provenance_values
            else "human_confirmed"
            if provenance_values == {"human_confirmed"}
            else "unknown_historical"
        )
        context = turns[max(0, index - context_turns) : index]
        episode = _episode_payload(
            turn=turn,
            incoming=incoming_turn,
            context=context,
            classification=classification,
            private_names=private_names,
        )
        duplicate_key = stable_fingerprint(
            {
                "incoming": episode["incoming"]["messages"],
                "target": episode["human_target"]["messages"],
            }
        )
        if duplicate_key in seen:
            excluded.append(
                {
                    "record_id": episode["example_id"],
                    "direction": "outgoing",
                    "timestamp": turn.first_timestamp,
                    "reasons": ["duplicate_episode"],
                }
            )
            continue
        seen.add(duplicate_key)
        episodes.append(episode)
    if len(episodes) > max_episodes:
        for item in episodes[:-max_episodes]:
            excluded.append(
                {
                    "record_id": item["example_id"],
                    "direction": "outgoing",
                    "timestamp": item["human_target"]["timestamps"][0],
                    "reasons": ["candidate_limit"],
                }
            )
        episodes = episodes[-max_episodes:]
    return episodes, excluded


def select_review_sample(
    episodes: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if limit <= 0 or not episodes:
        return []
    selected: list[dict[str, Any]] = []
    used: set[str] = set()
    predicates = (
        lambda item: sum(len(value) for value in item["human_target"]["messages"]) <= 12,
        lambda item: len(item["human_target"]["messages"]) > 1,
        lambda item: any(_starts_lower(value) for value in item["human_target"]["messages"]),
        lambda item: any(_starts_upper(value) for value in item["human_target"]["messages"]),
        lambda item: any(_contains_emoji(value) for value in item["human_target"]["messages"]),
        lambda item: any("?" in value for value in item["human_target"]["messages"]),
        lambda item: any("!" in value for value in item["human_target"]["messages"]),
        lambda item: any(len(value) >= 180 for value in item["human_target"]["messages"]),
        lambda item: any(
            bool(
                re.search(
                    r"(?i)\b(?:\u0431\u043b\u044f\w*|\u0445\u0443\u0439\w*|"
                    r"\u043f\u0438\u0437\u0434\w*|\u0435\u0431\w*)\b",
                    value,
                )
            )
            for value in item["human_target"]["messages"]
        ),
        lambda item: _episode_response_delay(item) > 3600,
    )
    for predicate in predicates:
        match = next(
            (
                item
                for item in episodes
                if item["example_id"] not in used and predicate(item)
            ),
            None,
        )
        if match is not None:
            selected.append(match)
            used.add(match["example_id"])
    for item in _evenly_spaced(episodes, limit):
        if len(selected) >= limit:
            break
        if item["example_id"] not in used:
            selected.append(item)
            used.add(item["example_id"])
    for item in episodes:
        if len(selected) >= min(limit, len(episodes)):
            break
        if item["example_id"] not in used:
            selected.append(item)
            used.add(item["example_id"])
    return selected


def compute_preview_fingerprint(path: Path) -> str:
    payload: dict[str, Any] = {}
    for name in (
        "manifest.json",
        "episodes.preview.jsonl",
        "excluded.jsonl",
        "style-profile-preview.json",
        "relationship-profile-preview.json",
        "privacy-report.json",
        "provenance-report.json",
        "exclusions-summary.json",
    ):
        file_path = path / name
        if not file_path.is_file():
            raise TelegramImportError(f"preview file is missing: {name}")
        if name.endswith(".jsonl"):
            value = [
                json.loads(line)
                for line in file_path.read_text(encoding="utf-8-sig").splitlines()
                if line.strip()
            ]
        else:
            value = json.loads(file_path.read_text(encoding="utf-8-sig"))
        if name == "manifest.json" and isinstance(value, dict):
            value.pop("preview_fingerprint", None)
        payload[name] = value
    return stable_fingerprint(payload)


def validate_preview(
    path: Path,
    *,
    expected_fingerprint: str | None = None,
) -> dict[str, Any]:
    errors: list[str] = []
    missing = sorted(name for name in REQUIRED_PREVIEW_FILES if not (path / name).is_file())
    errors.extend(f"missing:{name}" for name in missing)
    if missing:
        return {"valid": False, "errors": errors}
    recorded = (path / "preview-fingerprint.txt").read_text(
        encoding="utf-8-sig"
    ).strip()
    computed = compute_preview_fingerprint(path)
    if not recorded or recorded != computed:
        errors.append("preview_fingerprint_invalid")
    if expected_fingerprint is not None and computed != expected_fingerprint:
        errors.append("preview_fingerprint_mismatch")
    manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8-sig"))
    if manifest.get("confirmed_import") is not False:
        errors.append("preview_already_confirmed")
    if manifest.get("local_only") is not True or manifest.get("read_only") is not True:
        errors.append("privacy_boundary_invalid")
    for episode in _read_jsonl(path / "episodes.preview.jsonl"):
        if episode.get("source_type") != "imported_human_candidate":
            errors.append("invalid_preview_source_type")
        if episode.get("semantic_plan") is not None:
            errors.append("semantic_plan_must_be_null")
        if episode.get("incoming", {}).get("role") != "contact":
            errors.append("incoming_role_invalid")
        if episode.get("human_target", {}).get("role") != "human":
            errors.append("target_role_invalid")
    privacy = privacy_check(path)
    errors.extend(f"privacy:{item}" for item in privacy["errors"])
    return {
        "valid": not errors,
        "errors": sorted(set(errors)),
        "fingerprint": computed,
    }


def telegram_review_stats(path: Path) -> dict[str, Any]:
    decisions_path = path / "review-decisions.csv"
    if not decisions_path.is_file():
        raise TelegramImportError("review-decisions.csv is missing")
    with decisions_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    included = [item for item in rows if _csv_true(item.get("include"))]
    approved = [
        item
        for item in included
        if _csv_true(item.get("privacy_ok"))
        and _csv_true(item.get("provenance_ok"))
    ]
    return {
        "preview": str(path),
        "total": len(rows),
        "reviewed_include": len(included),
        "approved": len(approved),
        "excluded": sum(_csv_false(item.get("include")) for item in rows),
        "pending": sum(not str(item.get("include", "")).strip() for item in rows),
        "privacy_pending": sum(
            _csv_true(item.get("include"))
            and not str(item.get("privacy_ok", "")).strip()
            for item in rows
        ),
        "provenance_pending": sum(
            _csv_true(item.get("include"))
            and not str(item.get("provenance_ok", "")).strip()
            for item in rows
        ),
        "empty_is_approval": False,
    }


def confirm_telegram_preview(
    *,
    preview: Path,
    decisions: Path | None,
    fingerprint: str,
    consent_confirmed: bool,
    dataset_root: Path = Path("datasets/private-style"),
) -> dict[str, Any]:
    if not consent_confirmed:
        raise TelegramImportError("--consent-confirmed is required")
    if not preview.is_dir():
        raise TelegramImportError("existing preview is required")
    validation = validate_preview(preview, expected_fingerprint=fingerprint)
    if not validation["valid"]:
        raise TelegramImportError(
            "preview validation failed: " + ", ".join(validation["errors"])
        )
    decision_path = decisions or preview / "review-decisions.csv"
    if not decision_path.is_file():
        raise TelegramImportError("review decisions are required")
    with decision_path.open("r", encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    approved_ids = {
        str(item.get("example_id", ""))
        for item in rows
        if _csv_true(item.get("include"))
        and _csv_true(item.get("privacy_ok"))
        and _csv_true(item.get("provenance_ok"))
    }
    if not approved_ids:
        raise TelegramImportError(
            "no examples have include, privacy_ok and provenance_ok explicitly approved"
        )
    episodes = {
        str(item["example_id"]): item
        for item in _read_jsonl(preview / "episodes.preview.jsonl")
    }
    missing = sorted(approved_ids - episodes.keys())
    if missing:
        raise TelegramImportError("review decisions reference unknown example IDs")
    destination = dataset_root / "raw"
    destination.mkdir(parents=True, exist_ok=True)
    written: list[str] = []
    for example_id in sorted(approved_ids):
        payload = _confirmed_training_payload(episodes[example_id])
        target = destination / f"{example_id}.json"
        if target.exists():
            raise TelegramImportError(f"confirmed example already exists: {example_id}")
        _write_json(target, payload)
        written.append(example_id)
    manifest = json.loads((preview / "manifest.json").read_text(encoding="utf-8-sig"))
    confirmation = {
        "schema_version": 1,
        "confirmed_at": datetime.now(UTC).isoformat(),
        "preview_fingerprint": fingerprint,
        "approved_examples": len(written),
        "source_type": "imported_human_verified",
        "contact_alias": manifest.get("contact_alias"),
        "raw_identifiers_copied": False,
    }
    _write_json(dataset_root / "manifests" / f"telegram-{fingerprint[:12]}.json", confirmation)
    return {
        "confirmed": True,
        "examples": len(written),
        "preview_fingerprint": fingerprint,
        "dataset": str(dataset_root),
    }


def load_raw_messages(path: Path) -> list[RawTelegramMessage]:
    if not path.is_file():
        return []
    return [RawTelegramMessage.from_dict(item) for item in _read_jsonl(path)]


def _episode_payload(
    *,
    turn: MessageTurn,
    incoming: MessageTurn,
    context: list[MessageTurn],
    classification: ProvenanceClass,
    private_names: Iterable[str],
) -> dict[str, Any]:
    message_ids = [str(item.message_id) for item in turn.messages]
    example_id = "tg-" + stable_fingerprint(
        {
            "message_ids": message_ids,
            "timestamps": [item.timestamp for item in turn.messages],
        }
    )[:16]
    context_payload = [
        _turn_payload(item, private_names=private_names) for item in context
    ]
    target_payload = _turn_payload(turn, private_names=private_names)
    incoming_payload = _turn_payload(incoming, private_names=private_names)
    findings = [
        finding
        for item in (*incoming.messages, *turn.messages)
        for finding in scan_text(item.content, private_names=private_names)
    ]
    return {
        "example_id": example_id,
        "import_id": "telegram-private-preview",
        "agent_id": "private-agent",
        "contact_alias": "contact_private_001",
        "relationship_type": "private_contact",
        "context_turns": context_payload,
        "incoming": incoming_payload,
        "human_target": target_payload,
        "source_type": "imported_human_candidate",
        "semantic_plan": None,
        "semantic_enrichment_status": "pending",
        "provenance": {
            "telegram": True,
            "verified": classification in {"human_confirmed", "human_edited_ai"},
            "classification": classification,
            "message_ids": message_ids,
            "origin_checks": sorted(
                {
                    check
                    for item in turn.messages
                    for check in item.origin_checks
                }
            ),
            "caption_source": any(item.caption for item in turn.messages),
        },
        "privacy": {
            "pii_detected": bool(findings),
            "redactions": sorted({item.kind for item in findings}),
            "review_required": True,
        },
        "quality_flags": (
            ["unknown_provenance_review_required"]
            if classification == "unknown_historical"
            else []
        ),
    }


def _turn_payload(
    turn: MessageTurn,
    *,
    private_names: Iterable[str],
) -> dict[str, Any]:
    messages = [
        redact_text(item.content, private_names=private_names)[0]
        for item in turn.messages
    ]
    return {
        "role": turn.role,
        "messages": messages,
        "timestamps": [item.timestamp for item in turn.messages],
        "inter_bubble_delays_seconds": [
            round(_seconds_between(previous.timestamp, current.timestamp), 3)
            for previous, current in zip(turn.messages, turn.messages[1:])
        ],
        "media_types": [item.media_type for item in turn.messages],
    }


def _confirmed_training_payload(episode: dict[str, Any]) -> dict[str, Any]:
    context = [
        {
            "role": item["role"],
            "content": "\n".join(item.get("messages", [])),
        }
        for item in episode.get("context_turns", [])
    ]
    target = list(episode.get("human_target", {}).get("messages", []))
    timestamp = next(
        iter(episode.get("human_target", {}).get("timestamps", [])),
        datetime.now(UTC).isoformat(),
    )
    return {
        "example_id": episode["example_id"],
        "agent_id": episode.get("agent_id", "private-agent"),
        "conversation_context": context,
        "relationship_context": {
            "relationship_type": episode.get("relationship_type", "private_contact"),
            "contact_alias": episode.get("contact_alias", "contact_private_001"),
        },
        "semantic_plan": None,
        "semantic_enrichment_status": "pending",
        "adaptive_style_plan": {},
        "human_target_bubbles": target,
        "style_evidence": [
            {
                "source_type": "imported_human_verified",
                "origin": "human",
                "bubbles": target,
                "contact_alias": episode.get("contact_alias", "contact_private_001"),
            }
        ],
        "provenance": {
            "origin": "human",
            "verified": True,
            "purpose": "private_style",
            "source": "local_telegram_import",
            "raw_identifiers_included": False,
        },
        "timestamp": timestamp,
        "privacy_status": "approved",
        "approval_status": "approved",
        "source_type": "imported_human_verified",
        "quality_flags": list(episode.get("quality_flags", [])),
        "previous_candidate": [],
        "pii_flags": list(episode.get("privacy", {}).get("redactions", [])),
    }


def _summary_markdown(
    manifest: dict[str, Any],
    output: Path,
    fingerprint: str,
) -> str:
    provenance = manifest["provenance_counts"]
    command = _confirmation_command(output, fingerprint)
    return "\n".join(
        (
            "# Private Telegram import preview",
            "",
            f"- Masked account: {manifest['account']}",
            f"- Masked contact: {manifest['contact']['masked_display_name']}",
            "- Resolved contact verification: strict numeric match",
            f"- Resolved ID suffix: {manifest['contact']['resolved_id_suffix']}",
            f"- Requested limit: {manifest['requested_limit']}",
            f"- Fetched messages: {manifest['fetched_messages']}",
            f"- Date range: {manifest['date_range']['from']} to {manifest['date_range']['until']}",
            f"- Incoming: {manifest['incoming_count']}",
            f"- Outgoing: {manifest['outgoing_count']}",
            f"- Human-confirmed: {provenance.get('human_confirmed', 0)}",
            f"- AI-generated exclusions: {provenance.get('ai_generated', 0)}",
            f"- Unknown historical: {provenance.get('unknown_historical', 0)}",
            f"- Built turns: {manifest['built_turns']}",
            f"- Candidate episodes: {manifest['candidate_episodes']}",
            f"- Excluded records: {manifest['excluded_records']}",
            f"- PII flags: {manifest['privacy_finding_count']}",
            f"- Duplicates: {manifest['duplicate_count']}",
            f"- Media metadata: {manifest['media_metadata_count']}",
            f"- Preview fingerprint: `{fingerprint}`",
            "",
            "Review the sample and fill `review-decisions.csv`. Empty cells are not approval.",
            "",
            "Confirmation command (do not run before explicit review):",
            "",
            "```powershell",
            command,
            "```",
            "",
        )
    )


def _review_sample_markdown(episodes: list[dict[str, Any]]) -> str:
    lines = [
        "# Diverse private Telegram review sample",
        "",
        "Contact text is context only. Owner text is a candidate target.",
        "",
    ]
    for index, episode in enumerate(episodes, start=1):
        lines.extend(
            (
                f"## {index}. {episode['example_id']}",
                "",
                f"Provenance: `{episode['provenance']['classification']}`",
                "",
                "**CONTACT (context only)**",
                "",
            )
        )
        lines.extend(f"> {item}" for item in episode["incoming"]["messages"])
        lines.extend(("", "**OWNER (candidate bubbles)**", ""))
        lines.extend(
            f"{bubble_index}. {item}"
            for bubble_index, item in enumerate(
                episode["human_target"]["messages"],
                start=1,
            )
        )
        lines.append("")
    return "\n".join(lines)


def _write_review_decisions(path: Path, episodes: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "example_id",
                "include",
                "reason",
                "privacy_ok",
                "provenance_ok",
                "notes",
            ),
        )
        writer.writeheader()
        for item in episodes:
            writer.writerow(
                {
                    "example_id": item["example_id"],
                    "include": "",
                    "reason": "",
                    "privacy_ok": "",
                    "provenance_ok": "",
                    "notes": "",
                }
            )


def _confirmation_command(output: Path, fingerprint: str) -> str:
    return (
        "python -m conversation_agent dataset telegram-confirm "
        f'--preview "{output}" '
        f'--decisions "{output / "review-decisions.csv"}" '
        f"--fingerprint {fingerprint} --consent-confirmed"
    )


def _telegram_media_type(message: Any) -> str | None:
    media = getattr(message, "media", None)
    if media is None:
        return None
    name = type(media).__name__.casefold()
    document = getattr(media, "document", None)
    attributes = getattr(document, "attributes", ()) if document is not None else ()
    attribute_names = {type(item).__name__.casefold() for item in attributes}
    if "photo" in name:
        return "photo"
    if any("sticker" in item for item in attribute_names):
        return "sticker"
    if any("animated" in item for item in attribute_names) or "gif" in name:
        return "animation"
    if any("audio" in item for item in attribute_names):
        return (
            "voice"
            if any(bool(getattr(item, "voice", False)) for item in attributes)
            else "other"
        )
    if any("video" in item for item in attribute_names) or "video" in name:
        return "video"
    if "document" in name or document is not None:
        return "document"
    return "other"


def _masked_entity(entity: Any) -> str:
    username = str(getattr(entity, "username", "") or "")
    display = " ".join(
        str(getattr(entity, key, "") or "")
        for key in ("first_name", "last_name")
    ).strip()
    return _mask_value(username or display or type(entity).__name__)


def _entity_private_names(entity: Any) -> tuple[str, ...]:
    return tuple(
        value
        for value in (
            str(getattr(entity, "username", "") or "").strip(),
            str(getattr(entity, "first_name", "") or "").strip(),
            str(getattr(entity, "last_name", "") or "").strip(),
            " ".join(
                str(getattr(entity, key, "") or "").strip()
                for key in ("first_name", "last_name")
            ).strip(),
        )
        if value
    )


def _mask_value(value: str) -> str:
    compact = value.strip()
    if not compact:
        return "(not set)"
    if len(compact) == 1:
        return "*"
    if len(compact) == 2:
        return compact[0] + "*"
    return compact[0] + ("*" * min(8, len(compact) - 2)) + compact[-1]


def _extract_peer_id(peer: Any) -> int | None:
    for key in ("user_id", "chat_id", "channel_id"):
        value = _optional_int(getattr(peer, key, None))
        if value is not None:
            return value
    return _optional_int(peer)


def _session_file(path: Path) -> Path:
    return path if path.suffix == ".session" else path.with_suffix(".session")


def _isoformat(value: Any) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).isoformat()
    raise TelegramImportError("Telegram message timestamp is missing")


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _seconds_between(previous: str, current: str) -> float:
    first = _parse_datetime(previous)
    second = _parse_datetime(current)
    if first is None or second is None:
        return 0.0
    return max(0.0, (second - first).total_seconds())


def _text_hash(value: str) -> str:
    if not value.strip():
        return ""
    return hashlib.sha256(value.strip().encode("utf-8")).hexdigest()


def _private_record_id(message_id: int) -> str:
    return "record-" + hashlib.sha256(str(message_id).encode("ascii")).hexdigest()[:12]


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    payload = "\n".join(
        json.dumps(item, ensure_ascii=False, sort_keys=True) for item in rows
    )
    path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8-sig").splitlines()
        if line.strip()
    ]


def _optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    parsed = str(value)
    return parsed if parsed else None


def _csv_true(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y"}


def _csv_false(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"0", "false", "no", "n"}


def _starts_lower(value: str) -> bool:
    first = next((item for item in value if item.isalpha()), "")
    return bool(first and first.islower())


def _starts_upper(value: str) -> bool:
    first = next((item for item in value if item.isalpha()), "")
    return bool(first and first.isupper())


def _contains_emoji(value: str) -> bool:
    return bool(
        re.search(
            "[\U0001F1E6-\U0001F1FF\U0001F300-\U0001FAFF\u2600-\u27BF]",
            value,
        )
    )


def _episode_response_delay(episode: dict[str, Any]) -> float:
    incoming = episode.get("incoming", {}).get("timestamps", [])
    outgoing = episode.get("human_target", {}).get("timestamps", [])
    if not incoming or not outgoing:
        return 0.0
    return _seconds_between(str(incoming[-1]), str(outgoing[0]))


def _evenly_spaced(
    episodes: list[dict[str, Any]],
    limit: int,
) -> list[dict[str, Any]]:
    if len(episodes) <= limit:
        return list(episodes)
    if limit == 1:
        return [episodes[len(episodes) // 2]]
    indexes = {
        round(index * (len(episodes) - 1) / (limit - 1))
        for index in range(limit)
    }
    return [episodes[index] for index in sorted(indexes)]


def parse_optional_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = _parse_datetime(value)
    if parsed is None:
        raise TelegramImportError("date must be ISO-8601")
    return parsed


def run_telegram_preview_sync(options: TelegramPreviewOptions) -> dict[str, Any]:
    return asyncio.run(run_telegram_preview(options))
