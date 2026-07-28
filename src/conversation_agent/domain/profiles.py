"""Local editable profile loading without secret-bearing configuration."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypeVar

from conversation_agent.domain.models import BusinessProfile, IdentityProfile, StyleProfile

Profile = TypeVar("Profile", IdentityProfile, BusinessProfile, StyleProfile)


def load_identity_profile(path: Path) -> IdentityProfile:
    return IdentityProfile.from_dict(_load_mapping(path))


def load_business_profile(path: Path) -> BusinessProfile:
    return BusinessProfile.from_dict(_load_mapping(path))


def load_style_profile(path: Path) -> StyleProfile:
    return StyleProfile.from_dict(_load_mapping(path))


def _load_mapping(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ValueError(f"Profile file not found: {path}")
    suffix = path.suffix.lower()
    text = path.read_text(encoding="utf-8-sig")
    if suffix == ".json":
        value = json.loads(text)
        if not isinstance(value, dict):
            raise ValueError(f"Profile must contain a JSON object: {path}")
        return value
    if suffix in {".md", ".markdown"}:
        return _markdown_mapping(text)
    raise ValueError(f"Unsupported profile format: {path.suffix}; use JSON or Markdown")


def _markdown_mapping(text: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    current = "description"
    paragraphs: dict[str, list[str]] = {current: []}
    bullets: dict[str, list[str]] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if line.startswith("#"):
            current = line.lstrip("#").strip().lower().replace(" ", "_")
            paragraphs.setdefault(current, [])
            continue
        if line.startswith(("- ", "* ")):
            bullets.setdefault(current, []).append(line[2:].strip())
        elif line:
            paragraphs.setdefault(current, []).append(line)
    for key, lines in paragraphs.items():
        if lines:
            result[key] = "\n".join(lines)
    result.update(bullets)
    return result
