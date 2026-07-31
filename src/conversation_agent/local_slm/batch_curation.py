"""Deterministic Stage 3C batch review and curated confirmation gates."""

from __future__ import annotations

import csv
import json
from collections import Counter
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from conversation_agent.local_slm.authoritative_pilot import selection_fingerprint
from conversation_agent.local_slm.stage2_dataset import (
    registered_benchmark_fingerprints,
    stable_fingerprint,
)
from conversation_agent.local_slm.telegram_curation import (
    AUTHORITATIVE_HUMAN,
    BATCH_DECISIONS,
    PII_ACTIONS,
    TelegramCurationError,
    reconciliation_fingerprint,
)
from conversation_agent.local_slm.telegram_import import select_review_sample
from conversation_agent.local_slm.telegram_style_profile import (
    PROFILE_SCHEMA_VERSION,
    build_style_profiles,
    dataset_rows_to_profile_episodes,
)

DEFAULT_DATASET_ROOT = Path("datasets/private-style")


def build_batch_review(
    *,
    reconciliation: Path,
    output: Path,
    max_batch_size: int,
) -> dict[str, Any]:
    if max_batch_size < 1:
        raise TelegramCurationError("--max-batch-size must be greater than zero")
    fingerprint = _validated_reconciliation_fingerprint(reconciliation)
    episodes = _read_jsonl(reconciliation / "episodes.reconciled.jsonl")
    unknown = [
        item
        for item in episodes
        if item.get("stage3c", {}).get("classification") == "unknown_historical"
    ]
    unknown.sort(key=_episode_timestamp)
    batches = _group_unknown_episodes(unknown, max_batch_size=max_batch_size)
    pii_records = _read_jsonl(reconciliation / "pii-records.jsonl")
    pii_count = sum(
        item.get("pii_type") != "sensitive_self_harm" for item in pii_records
    )
    sensitive_count = sum(
        item.get("pii_type") == "sensitive_self_harm" for item in pii_records
    )
    pii_by_episode = _pii_by_episode(pii_records)
    output.mkdir(parents=True, exist_ok=True)
    batch_rows: list[dict[str, Any]] = []
    batch_manifest: list[dict[str, Any]] = []
    for index, batch in enumerate(batches, start=1):
        batch_id = f"batch-{index:03d}"
        episode_ids = [str(item["example_id"]) for item in batch]
        flags = Counter(
            flag
            for item in batch
            for flag in item.get("stage3c", {}).get("heuristic_flags", [])
        )
        batch_manifest.append(
            {
                "batch_id": batch_id,
                "episode_ids": episode_ids,
                "date_range": {
                    "from": _masked_date(_episode_timestamp(batch[0])),
                    "until": _masked_date(_episode_timestamp(batch[-1])),
                },
                "messages": sum(
                    len(item.get("human_target", {}).get("messages", []))
                    for item in batch
                ),
                "episodes": len(batch),
                "unknown_count": len(batch),
                "ai_matches": 0,
                "human_matches": 0,
                "heuristic_flags": dict(sorted(flags.items())),
                "pii_count": sum(
                    len(pii_by_episode.get(item, [])) for item in episode_ids
                ),
                "review_priority": max(
                    (
                        int(item.get("stage3c", {}).get("review_priority", 0))
                        for item in batch
                    ),
                    default=0,
                ),
            }
        )
        batch_rows.append(
            {
                "batch_id": batch_id,
                "decision": "",
                "reason": "",
                "consent_ok": "",
                "privacy_ok": "",
                "notes": "",
            }
        )
    authoritative_human = [
        item
        for item in episodes
        if item.get("stage3c", {}).get("classification") in AUTHORITATIVE_HUMAN
    ]
    confirmed_ai = [
        item
        for item in episodes
        if item.get("stage3c", {}).get("classification") == "ai_generated"
    ]
    conflicting = [
        item
        for item in episodes
        if item.get("stage3c", {}).get("classification") == "conflicting_evidence"
    ]
    ambiguous = sorted(
        unknown,
        key=lambda item: int(item.get("stage3c", {}).get("review_priority", 0)),
        reverse=True,
    )[:50]
    pilot_pool = [
        item
        for item in authoritative_human
        if not pii_by_episode.get(str(item["example_id"]))
    ]
    proposed = select_review_sample(pilot_pool, limit=min(100, len(pilot_pool)))
    _write_csv(
        output / "batch-decisions.csv",
        (
            "batch_id",
            "decision",
            "reason",
            "consent_ok",
            "privacy_ok",
            "notes",
        ),
        batch_rows,
    )
    _write_csv(
        output / "pii-review.csv",
        (
            "record_id",
            "pii_type",
            "suggested_action",
            "approved_action",
            "notes",
        ),
        [
            {
                "record_id": item["record_id"],
                "pii_type": item["pii_type"],
                "suggested_action": item["suggested_action"],
                "approved_action": "",
                "notes": "",
            }
            for item in pii_records
        ],
    )
    _write_review_markdown(output / "batches.md", batch_manifest, batches)
    _write_episode_markdown(
        output / "ambiguous-examples.md",
        "Ambiguous examples requiring careful review",
        ambiguous,
    )
    _write_episode_markdown(
        output / "confirmed-ai.md",
        "Authoritative AI contamination",
        confirmed_ai,
    )
    _write_episode_markdown(
        output / "confirmed-human.md",
        "Authoritative human examples",
        authoritative_human,
    )
    _write_episode_markdown(
        output / "conflicting-evidence.md",
        "Conflicting evidence",
        conflicting,
    )
    _write_episode_markdown(
        output / "suggested-first-pilot.md",
        "Unconfirmed balanced first-pilot proposal",
        proposed,
    )
    summary = {
        "schema_version": 1,
        "reconciliation_fingerprint": fingerprint,
        "batch_count": len(batches),
        "max_batch_size": max_batch_size,
        "unknown_episodes": len(unknown),
        "authoritative_human_episodes": len(authoritative_human),
        "confirmed_ai_episodes": len(confirmed_ai),
        "conflicting_episodes": len(conflicting),
        "pii_findings": pii_count,
        "sensitive_content_findings": sensitive_count,
        "suggested_first_pilot_size": len(proposed),
        "batch_decisions_pending": len(batches),
        "pii_decisions_pending": len(pii_records),
        "batches": batch_manifest,
    }
    _write_json(output / "provenance-summary.json", summary)
    (output / "reconciliation-fingerprint.txt").write_text(
        fingerprint + "\n",
        encoding="utf-8",
    )
    (output / "README.md").write_text(
        "# Stage 3C batch review\n\n"
        "Review batches instead of approving individual rows by default.\n"
        "Empty decisions are not approval. Heuristics only affect ordering.\n"
        "Authoritative AI and conflicts cannot be included as human targets.\n",
        encoding="utf-8",
    )
    (output / "curation-summary.md").write_text(
        _curation_summary_markdown(summary),
        encoding="utf-8",
    )
    return {
        "output": str(output),
        "reconciliation_fingerprint": fingerprint,
        "batch_count": len(batches),
        "unknown_episodes": len(unknown),
        "confirmed_ai_episodes": len(confirmed_ai),
        "authoritative_human_episodes": len(authoritative_human),
        "conflicting_episodes": len(conflicting),
        "pii_findings": pii_count,
        "sensitive_content_findings": sensitive_count,
        "suggested_first_pilot_size": len(proposed),
    }


def batch_review_stats(batch_review: Path) -> dict[str, Any]:
    rows = _read_csv(batch_review / "batch-decisions.csv")
    decisions = Counter(
        str(item.get("decision", "")).strip() or "pending" for item in rows
    )
    invalid = sorted(
        {
            value
            for value in decisions
            if value not in BATCH_DECISIONS and value != "pending"
        }
    )
    included = [
        item
        for item in rows
        if item.get("decision") == "include_human"
        and _csv_true(item.get("consent_ok"))
        and _csv_true(item.get("privacy_ok"))
    ]
    return {
        "total_batches": len(rows),
        "decisions": dict(sorted(decisions.items())),
        "approved_human_batches": len(included),
        "invalid_decisions": invalid,
        "empty_is_approval": False,
    }


def confirm_curated_dataset(
    *,
    preview: Path,
    reconciliation: Path,
    batch_decisions: Path | None,
    pilot_selection: Path | None = None,
    pii_decisions: Path,
    fingerprint: str,
    consent_confirmed: bool,
    authoritative_only: bool = False,
    max_examples: int,
    dataset_root: Path = DEFAULT_DATASET_ROOT,
) -> dict[str, Any]:
    if not consent_confirmed:
        raise TelegramCurationError("--consent-confirmed is required")
    maximum = 82 if authoritative_only else 100
    if not 50 <= max_examples <= maximum:
        raise TelegramCurationError(f"--max-examples must be between 50 and {maximum}")
    reconciliation_recorded = _validated_reconciliation_fingerprint(reconciliation)
    if authoritative_only:
        if pilot_selection is None:
            raise TelegramCurationError(
                "--pilot-selection is required with --authoritative-only"
            )
        recorded = selection_fingerprint(pilot_selection)
        if fingerprint != recorded:
            raise TelegramCurationError("pilot selection fingerprint does not match")
    else:
        recorded = reconciliation_recorded
        if fingerprint != recorded:
            raise TelegramCurationError("reconciliation fingerprint does not match")
        if batch_decisions is None:
            raise TelegramCurationError("--batch-decisions is required")
    source_preview = json.loads(
        (reconciliation / "manifest.json").read_text(encoding="utf-8-sig")
    ).get("source_preview_fingerprint")
    current_preview = (
        (preview / "preview-fingerprint.txt").read_text(encoding="utf-8-sig").strip()
    )
    if source_preview != current_preview:
        raise TelegramCurationError("Stage 3B preview does not match reconciliation")
    if source_preview in registered_benchmark_fingerprints():
        raise TelegramCurationError("benchmark data cannot be curated as training data")
    episodes = {
        str(item["example_id"]): item
        for item in _read_jsonl(reconciliation / "episodes.reconciled.jsonl")
    }
    approved_unknown: set[str] = set()
    if not authoritative_only:
        assert batch_decisions is not None
        summary = json.loads(
            (batch_decisions.parent / "provenance-summary.json").read_text(
                encoding="utf-8-sig"
            )
        )
        batch_map = {
            str(item["batch_id"]): [str(value) for value in item.get("episode_ids", [])]
            for item in summary.get("batches", [])
        }
        for row in _read_csv(batch_decisions):
            decision = str(row.get("decision", "")).strip()
            if decision and decision not in BATCH_DECISIONS:
                raise TelegramCurationError(f"invalid batch decision: {decision}")
            if decision == "include_human":
                if not _csv_true(row.get("consent_ok")) or not _csv_true(
                    row.get("privacy_ok")
                ):
                    raise TelegramCurationError(
                        "include_human batch requires consent_ok and privacy_ok"
                    )
                approved_unknown.update(batch_map.get(str(row.get("batch_id")), []))
    authoritative = {
        example_id
        for example_id, episode in episodes.items()
        if episode.get("stage3c", {}).get("classification") in AUTHORITATIVE_HUMAN
        and episode.get("stage3c", {}).get("authoritative") is True
    }
    if authoritative_only:
        assert pilot_selection is not None
        selected_rows = _read_jsonl(pilot_selection / "selected.preview.jsonl")
        if any(
            str(item.get("source_reconciliation_fingerprint", ""))
            != reconciliation_recorded
            for item in selected_rows
        ):
            raise TelegramCurationError("pilot selection does not match reconciliation")
        requested_ids = [str(item["example_id"]) for item in selected_rows]
        if len(requested_ids) != len(set(requested_ids)):
            raise TelegramCurationError("pilot selection contains duplicate examples")
        unknown_ids = [item for item in requested_ids if item not in episodes]
        if unknown_ids:
            raise TelegramCurationError("pilot selection contains unknown examples")
        eligible = [
            episodes[example_id]
            for example_id in requested_ids
            if example_id in authoritative
        ]
        if len(eligible) != len(requested_ids):
            raise TelegramCurationError(
                "pilot selection contains non-authoritative examples"
            )
    else:
        eligible_ids = authoritative | approved_unknown
        eligible = [
            item
            for example_id, item in episodes.items()
            if example_id in eligible_ids
            and item.get("stage3c", {}).get("classification")
            not in {"ai_generated", "conflicting_evidence"}
        ]
    if not eligible:
        raise TelegramCurationError("no human examples are explicitly eligible")
    pii_rows = _read_csv(pii_decisions)
    pii_record_map = {
        str(item["record_id"]): item
        for item in _read_jsonl(reconciliation / "pii-records.jsonl")
    }
    approved_pii: dict[str, str] = {}
    for row in pii_rows:
        action = str(row.get("approved_action", "")).strip()
        if action and action not in PII_ACTIONS:
            raise TelegramCurationError(f"invalid PII action: {action}")
        if action:
            approved_pii[str(row.get("record_id"))] = action
    eligible_ids = {str(item["example_id"]) for item in eligible}
    unresolved_ids = {
        str(episode_id)
        for record_id, item in pii_record_map.items()
        if record_id not in approved_pii
        for episode_id in item.get("episode_ids", [])
        if str(episode_id) in eligible_ids
    }
    if unresolved_ids and not authoritative_only:
        raise TelegramCurationError("unresolved PII blocks curated confirmation")
    eligible = [
        item for item in eligible if str(item["example_id"]) not in unresolved_ids
    ]
    excluded_by_action = {
        str(episode_id)
        for record_id, action in approved_pii.items()
        if action == "exclude"
        for episode_id in pii_record_map.get(record_id, {}).get("episode_ids", [])
    }
    eligible = [
        item for item in eligible if str(item["example_id"]) not in excluded_by_action
    ]
    if not eligible:
        raise TelegramCurationError("no human examples remain after PII review")
    selected = (
        eligible[:max_examples]
        if authoritative_only
        else select_review_sample(eligible, limit=max_examples)
    )
    destination = dataset_root / "raw" / f"curated-{fingerprint[:12]}"
    if destination.exists():
        raise TelegramCurationError("curated destination already exists")
    destination.mkdir(parents=True, exist_ok=False)
    payloads = [
        _curated_training_payload(
            item,
            user_batch_approved=str(item["example_id"]) in approved_unknown,
            pii_transformations=_episode_pii_transformations(
                str(item["example_id"]),
                pii_record_map,
                approved_pii,
            ),
            authoritative_only=authoritative_only,
        )
        for item in selected
    ]
    _write_jsonl(destination / "examples.jsonl", payloads)
    dataset_fingerprint = stable_fingerprint(payloads)
    manifest = {
        "schema_version": 1,
        "created_at": datetime.now(UTC).isoformat(),
        "source_reconciliation_fingerprint": reconciliation_recorded,
        "pilot_selection_fingerprint": (fingerprint if authoritative_only else None),
        "dataset_fingerprint": dataset_fingerprint,
        "examples": len(payloads),
        "max_examples": max_examples,
        "source_type": "imported_human_verified",
        "benchmark_data_allowed": False,
        "contact_identifiers_included": False,
        "training_performed": False,
        "authoritative_only": authoritative_only,
        "unresolved_pii_examples_skipped": len(unresolved_ids),
    }
    _write_json(destination / "manifest.json", manifest)
    return {
        "confirmed": True,
        "dataset": str(destination),
        "examples": len(payloads),
        "dataset_fingerprint": dataset_fingerprint,
        "training_performed": False,
        "unresolved_pii_examples_skipped": len(unresolved_ids),
    }


def build_curated_style_profiles(
    *,
    dataset: Path,
    output: Path,
) -> dict[str, Any]:
    rows = _load_dataset_rows(dataset)
    eligible_rows: list[dict[str, Any]] = []
    for row in rows:
        source_type = str(row.get("source_type", ""))
        provenance = row.get("provenance", {})
        origin_class = str(provenance.get("classification", ""))
        if source_type != "imported_human_verified":
            continue
        if provenance.get("verified") is not True:
            continue
        if origin_class not in {
            "human_confirmed",
            "human_edited_ai",
            "user_approved_unknown_batch",
            "human_manual",
            "human_fix",
        }:
            continue
        eligible_rows.append(row)
    episodes = dataset_rows_to_profile_episodes(eligible_rows)
    agent, relationship = build_style_profiles(
        episodes,
        agent_id="private-agent",
        relationship_id="private_contact",
        generated_at=datetime.now(UTC).isoformat(),
    )
    output.mkdir(parents=True, exist_ok=True)
    _write_json(output / "agent-style-profile.json", agent)
    _write_json(output / "relationship-style-profile.json", relationship)
    manifest = {
        "schema_version": PROFILE_SCHEMA_VERSION,
        "source_examples": len(rows),
        "eligible_human_examples": len(episodes),
        "owner_message_bubbles": sum(
            len(item.get("human_target", {}).get("messages", []))
            for item in episodes
        ),
        "relationship_count": len(
            {str(item.get("relationship_id", "")) for item in episodes}
        ),
        "contact_count": len(
            {str(item.get("contact_alias", "")) for item in episodes}
        ),
        "fixed_rules": [],
        "profiles_are_distributions": True,
        "timing_values_invented": False,
        "target_text_normalized": False,
        "dataset_fingerprint": stable_fingerprint(rows),
    }
    _write_json(output / "manifest.json", manifest)
    return {**manifest, "output": str(output)}


def _group_unknown_episodes(
    episodes: list[dict[str, Any]],
    *,
    max_batch_size: int,
) -> list[list[dict[str, Any]]]:
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    previous_timestamp: datetime | None = None
    previous_flags: set[str] = set()
    for episode in episodes:
        timestamp = _parse_datetime(_episode_timestamp(episode))
        flags = set(episode.get("stage3c", {}).get("heuristic_flags", []))
        gap = (
            (timestamp - previous_timestamp).total_seconds()
            if timestamp is not None and previous_timestamp is not None
            else 0
        )
        style_boundary = bool(
            current
            and previous_flags
            and flags
            and not previous_flags.intersection(flags)
            and gap > 1800
        )
        if current and (
            len(current) >= max_batch_size or gap > 21600 or style_boundary
        ):
            batches.append(current)
            current = []
        current.append(episode)
        previous_timestamp = timestamp
        previous_flags = flags
    if current:
        batches.append(current)
    return batches


def _curated_training_payload(
    episode: dict[str, Any],
    *,
    user_batch_approved: bool,
    pii_transformations: list[dict[str, str]] | None = None,
    authoritative_only: bool = False,
) -> dict[str, Any]:
    context = [
        {
            "role": item.get("role"),
            "content": "\n".join(item.get("messages", []))
            if isinstance(item.get("messages"), list)
            else str(item.get("content", "")),
        }
        for item in episode.get("context_turns", [])
    ]
    target = list(episode.get("human_target", {}).get("messages", []))
    original = str(episode.get("stage3c", {}).get("classification", ""))
    classification = "user_approved_unknown_batch" if user_batch_approved else original
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
            "classification": classification,
            "verified": True,
            "purpose": "private_style",
            "source": "stage3c_curated_telegram_import",
            "authoritative": authoritative_only or original in AUTHORITATIVE_HUMAN,
            "raw_identifiers_included": False,
            "user_batch_approved": user_batch_approved,
        },
        "timestamp": _episode_timestamp(episode),
        "privacy_status": "approved",
        "approval_status": "approved",
        "source_type": "imported_human_verified",
        "quality_flags": [
            item
            for item in episode.get("quality_flags", [])
            if item != "unknown_provenance_review_required"
        ],
        "previous_candidate": [],
        "pii_flags": [],
        "pii_transformations": pii_transformations or [],
    }


def _episode_pii_transformations(
    example_id: str,
    pii_records: dict[str, dict[str, Any]],
    approved_pii: dict[str, str],
) -> list[dict[str, str]]:
    return [
        {
            "record_id": record_id,
            "pii_type": str(record.get("pii_type", "")),
            "action": approved_pii[record_id],
        }
        for record_id, record in pii_records.items()
        if example_id in {str(value) for value in record.get("episode_ids", [])}
        and record_id in approved_pii
    ]


def _write_review_markdown(
    path: Path,
    manifests: list[dict[str, Any]],
    batches: list[list[dict[str, Any]]],
) -> None:
    lines = ["# Batch review", ""]
    for manifest, batch in zip(manifests, batches):
        lines.extend(
            (
                f"## {manifest['batch_id']}",
                "",
                (
                    f"- Masked date range: {manifest['date_range']['from']} to "
                    f"{manifest['date_range']['until']}"
                ),
                f"- Messages: {manifest['messages']}",
                f"- Episodes: {manifest['episodes']}",
                f"- AI matches: {manifest['ai_matches']}",
                f"- Human matches: {manifest['human_matches']}",
                f"- Unknown: {manifest['unknown_count']}",
                f"- PII findings: {manifest['pii_count']}",
                f"- Heuristic flags: {manifest['heuristic_flags']}",
                "",
                "Representative pseudonymized examples:",
                "",
            )
        )
        for episode in select_review_sample(batch, limit=min(3, len(batch))):
            target = " | ".join(episode.get("human_target", {}).get("messages", []))
            lines.append(f"- `{episode['example_id']}`: {target}")
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_episode_markdown(
    path: Path,
    title: str,
    episodes: list[dict[str, Any]],
) -> None:
    lines = [f"# {title}", ""]
    for episode in episodes:
        classification = episode.get("stage3c", {}).get("classification", "unknown")
        lines.extend(
            (
                f"## {episode['example_id']}",
                "",
                f"Classification: `{classification}`",
                "",
                "**CONTACT (context only)**",
                "",
            )
        )
        lines.extend(
            f"> {item}" for item in episode.get("incoming", {}).get("messages", [])
        )
        lines.extend(("", "**OWNER (candidate)**", ""))
        lines.extend(
            f"- {item}" for item in episode.get("human_target", {}).get("messages", [])
        )
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def _curation_summary_markdown(summary: dict[str, Any]) -> str:
    return "\n".join(
        (
            "# Stage 3C curation summary",
            "",
            f"- Batches: {summary['batch_count']}",
            f"- Unknown episodes: {summary['unknown_episodes']}",
            f"- Authoritative human episodes: {summary['authoritative_human_episodes']}",
            f"- Confirmed AI episodes: {summary['confirmed_ai_episodes']}",
            f"- Conflicting episodes: {summary['conflicting_episodes']}",
            f"- PII findings: {summary['pii_findings']}",
            f"- Suggested first pilot: {summary['suggested_first_pilot_size']}",
            "",
            "No batch or PII decision has been approved automatically.",
            "",
        )
    )


def _pii_by_episode(records: Iterable[dict[str, Any]]) -> dict[str, list[str]]:
    output: dict[str, list[str]] = {}
    for item in records:
        for episode_id in item.get("episode_ids", []):
            output.setdefault(str(episode_id), []).append(str(item["record_id"]))
    return output


def _masked_date(value: str) -> str:
    parsed = _parse_datetime(value)
    return parsed.strftime("%Y-%m-%d") if parsed is not None else "unknown-date"


def _episode_timestamp(episode: dict[str, Any]) -> str:
    timestamps = episode.get("human_target", {}).get("timestamps", [])
    return str(timestamps[0]) if timestamps else ""


def _parse_datetime(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _validated_reconciliation_fingerprint(reconciliation: Path) -> str:
    path = reconciliation / "reconciliation-fingerprint.txt"
    if not path.is_file():
        raise TelegramCurationError("reconciliation fingerprint is missing")
    recorded = path.read_text(encoding="utf-8-sig").strip()
    computed = reconciliation_fingerprint(reconciliation)
    if not recorded or recorded != computed:
        raise TelegramCurationError("reconciliation fingerprint is invalid")
    return recorded


def _load_dataset_rows(path: Path) -> list[dict[str, Any]]:
    files = [path] if path.is_file() else sorted(path.rglob("*.json*"))
    rows: list[dict[str, Any]] = []
    for file_path in files:
        if file_path.name == "manifest.json":
            continue
        if file_path.suffix.casefold() == ".jsonl":
            rows.extend(_read_jsonl(file_path))
        else:
            value = json.loads(file_path.read_text(encoding="utf-8-sig"))
            if isinstance(value, dict):
                rows.append(value)
            elif isinstance(value, list):
                rows.extend(item for item in value if isinstance(item, dict))
    return rows


def _write_csv(
    path: Path,
    fieldnames: tuple[str, ...],
    rows: Iterable[dict[str, Any]],
) -> None:
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _read_csv(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        raise TelegramCurationError(f"required decisions file is missing: {path.name}")
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        return [dict(item) for item in csv.DictReader(handle)]


def _csv_true(value: Any) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "y"}


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
