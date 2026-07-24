"""Load and validate private style artifacts from local runtime storage."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from conversation_agent.style.models import StyleBundle, StyleExample, StyleRule


def load_style_bundle(directory: Path, *, contact_id: int) -> StyleBundle:
    rules_path = directory / "matvey_behavior_rules.md"
    profile_path = directory / "style_profile.json"
    bank_path = directory / "example_bank.jsonl"
    summary_path = directory / "build_summary.json"
    contact_path = directory / "contacts" / f"{contact_id}.json"
    required = (rules_path, profile_path, bank_path, summary_path, contact_path)
    missing = [str(path) for path in required if not path.is_file()]
    if missing:
        raise ValueError(
            "Required style bundle is missing. Run: "
            "scripts\\build_style_bundle.bat. Missing: "
            + ", ".join(missing)
        )

    profile = _read_object(profile_path)
    summary = _read_object(summary_path)
    contact = _read_object(contact_path)
    examples = tuple(
        StyleExample.from_dict(json.loads(line))
        for line in bank_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    )
    rules = tuple(StyleRule.from_dict(item) for item in profile.get("rules", []))
    rules_markdown = rules_path.read_text(encoding="utf-8").strip()
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


def _read_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise TypeError(f"Expected JSON object: {path}")
    return value
