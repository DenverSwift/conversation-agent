"""Load and validate private style artifacts from local runtime storage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from conversation_agent.style.compiler_state import load_compiler_artifacts
from conversation_agent.style.models import StyleBundle, StyleExample, StyleRule


def load_style_bundle(
    directory: Path,
    *,
    contact_id: int,
    state_path: Path | None = None,
) -> StyleBundle:
    state_path = state_path or directory / "compiler_state.sqlite3"
    artifacts = load_compiler_artifacts(state_path)
    rules_path = directory / "matvey_behavior_rules.md"
    profile_path = directory / "style_profile.json"
    bank_path = directory / "example_bank.jsonl"
    summary_path = directory / "build_summary.json"
    contact_path = directory / "contacts" / f"{contact_id}.json"
    required = (rules_path, profile_path, bank_path, summary_path, contact_path)
    relative_paths = (
        "matvey_behavior_rules.md",
        "style_profile.json",
        "example_bank.jsonl",
        "build_summary.json",
        f"contacts/{contact_id}.json",
    )
    missing = [
        str(path)
        for path, relative in zip(required, relative_paths, strict=True)
        if relative not in artifacts and not path.is_file()
    ]
    if missing:
        raise ValueError(
            "Required style bundle is missing. Run: "
            "scripts\\build_style_bundle.bat. Missing: "
            + ", ".join(missing)
        )

    profile = _read_object_content(artifacts.get("style_profile.json"), profile_path)
    summary = _read_object_content(artifacts.get("build_summary.json"), summary_path)
    contact = _read_object_content(
        artifacts.get(f"contacts/{contact_id}.json"),
        contact_path,
    )
    bank_text = _text_content(artifacts.get("example_bank.jsonl"), bank_path)
    examples = tuple(
        StyleExample.from_dict(json.loads(line))
        for line in bank_text.splitlines()
        if line.strip()
    )
    rules = tuple(StyleRule.from_dict(item) for item in profile.get("rules", []))
    rules_markdown = _text_content(
        artifacts.get("matvey_behavior_rules.md"),
        rules_path,
    ).strip()
    if not rules_markdown or not rules:
        raise ValueError("Style bundle contains no compiled behavior rules")
    return StyleBundle(
        rules_markdown=rules_markdown,
        rules=rules,
        examples=examples,
        contact_profiles={contact_id: contact},
        built_at=str(summary["built_at"]),
        source_example_count=int(summary["source_example_count"]),
        batch_count=int(summary["batch_count"]),
    )


def load_manual_overrides(directory: Path) -> str:
    path = directory / "manual_overrides.md"
    return path.read_text(encoding="utf-8").strip() if path.is_file() else ""


def _read_object_content(content: bytes | None, fallback: Path) -> dict[str, Any]:
    value = json.loads(
        content.decode("utf-8")
        if content is not None
        else fallback.read_text(encoding="utf-8")
    )
    if not isinstance(value, dict):
        raise TypeError(f"Expected JSON object: {fallback}")
    return value


def _text_content(content: bytes | None, fallback: Path) -> str:
    return (
        content.decode("utf-8")
        if content is not None
        else fallback.read_text(encoding="utf-8")
    )
