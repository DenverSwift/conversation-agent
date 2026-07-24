"""Stable source identity and hashing for incremental style compilation."""

from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass, replace
from typing import Any

from conversation_agent.style.models import StyleExample

NORMALIZATION_VERSION = "1"
_WHITESPACE = re.compile(r"[ \t]+")


@dataclass(frozen=True)
class SourceScan:
    examples: tuple[StyleExample, ...]
    invalid_sources: int


def canonicalize_example(example: StyleExample) -> StyleExample:
    source_key = example.source_key or _source_key(example)
    canonical = {
        "source_type": example.source_type,
        "contact_id": example.contact_id,
        "incoming_text": _normalize_text(example.incoming_text),
        "target_reply": _normalize_text(example.response_text),
        "feedback_status": example.feedback_status,
        "feedback_category": example.feedback_category,
        "context": [
            {
                "role": item.get("role", ""),
                "text": _normalize_text(item.get("text", "")),
                "provenance": item.get("provenance", ""),
            }
            for item in example.context
        ],
        "provenance": example.provenance,
        "polarity": example.polarity,
    }
    serialized = json.dumps(
        canonical,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    content_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return replace(
        example,
        source_key=source_key,
        content_hash=content_hash,
        incoming_text=canonical["incoming_text"],
        response_text=canonical["target_reply"],
        context=tuple(canonical["context"]),
    )


def canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _source_key(example: StyleExample) -> str:
    if example.feedback_id is not None:
        suffix = {
            "fix": "fix",
            "approved_ai": "good",
            "should_not_reply": "should_not_reply",
        }.get(example.source_type, "rejected")
        return f"feedback:{example.feedback_id}:{suffix}"
    return f"telegram:{example.contact_id}:{example.example_id}"


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFC", value.replace("\r\n", "\n").replace("\r", "\n"))
    return "\n".join(_WHITESPACE.sub(" ", line).strip() for line in normalized.split("\n")).strip()
