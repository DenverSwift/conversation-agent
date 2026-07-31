"""Deterministic local privacy scanning for private Telegram imports."""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


@dataclass(frozen=True)
class PrivacyFinding:
    kind: str
    start: int
    end: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("email", re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b")),
    ("telegram_username", re.compile(r"(?<![\w@])@[A-Za-z][A-Za-z0-9_]{4,31}\b")),
    (
        "payment_card",
        re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)"),
    ),
    (
        "phone",
        re.compile(r"(?<!\d)(?:\+?\d[\s().-]*){9,15}(?!\d)"),
    ),
    (
        "api_key",
        re.compile(
            r"(?i)(?:sk-[A-Za-z0-9_-]{16,}|"
            r"(?:api[_ -]?key|secret[_ -]?key)\s*[:=]\s*\S{8,})"
        ),
    ),
    (
        "bot_token",
        re.compile(r"(?<!\d)\d{7,12}:[A-Za-z0-9_-]{20,}(?![A-Za-z0-9_-])"),
    ),
    (
        "password",
        re.compile(
            r"(?i)(?:password|passwd|pwd|\u043f\u0430\u0440\u043e\u043b\u044c)"
            r"\s*[:=]\s*\S{4,}"
        ),
    ),
    (
        "session_string",
        re.compile(r"(?i)(?:session(?:_string)?\s*[:=]\s*)[A-Za-z0-9+/=_-]{20,}"),
    ),
    (
        "passport",
        re.compile(
            r"(?i)(?:passport|\u043f\u0430\u0441\u043f\u043e\u0440\u0442)"
            r"\s*(?:no\.?|number|[\u2116:#])?\s*[A-Z\u0410-\u042f0-9 -]{6,20}"
        ),
    ),
    (
        "bank_details",
        re.compile(
            r"(?i)\b(?:IBAN|SWIFT|BIC|"
            r"\u0438\u043d\u043d|\u043a\u043f\u043f|"
            r"\u0440\/\u0441|\u043a\/\u0441)\b\s*[:=]?\s*[A-Z0-9 -]{6,34}"
        ),
    ),
    (
        "address",
        re.compile(
            r"(?i)\b(?:street|avenue|road|"
            r"\u0443\u043b(?:\u0438\u0446\u0430)?\.?|"
            r"\u043f\u0440\u043e\u0441\u043f\u0435\u043a\u0442|"
            r"\u0434\u043e\u043c|"
            r"\u043a\u0432(?:\u0430\u0440\u0442\u0438\u0440\u0430)?\.?)"
            r"\s+[A-Z\u0410-\u042f\u0430-\u044f0-9][^,\n]{2,50}"
        ),
    ),
)

_SECRET_QUERY_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "apikey",
        "auth",
        "code",
        "key",
        "password",
        "secret",
        "session",
        "sig",
        "signature",
        "token",
    }
)
_URL_PATTERN = re.compile(r"https?://[^\s<>\"]+", re.IGNORECASE)
_LONG_NUMERIC_ID = re.compile(r"(?<!\d)\d{7,15}(?!\d)")


def scan_text(text: str, *, private_names: Iterable[str] = ()) -> tuple[PrivacyFinding, ...]:
    findings: list[PrivacyFinding] = []
    occupied: list[tuple[int, int]] = []
    for kind, pattern in _PATTERNS:
        for match in pattern.finditer(text):
            if kind == "payment_card" and not _looks_like_card(match.group(0)):
                continue
            _append_finding(findings, occupied, kind, match.start(), match.end())
    for match in _URL_PATTERN.finditer(text):
        if _url_has_secret(match.group(0)):
            _append_finding(
                findings,
                occupied,
                "secret_url",
                match.start(),
                match.end(),
            )
    for match in _LONG_NUMERIC_ID.finditer(text):
        _append_finding(
            findings,
            occupied,
            "numeric_id",
            match.start(),
            match.end(),
        )
    for raw_name in private_names:
        name = raw_name.strip()
        if len(name) < 2:
            continue
        for match in re.finditer(re.escape(name), text, re.IGNORECASE):
            _append_finding(
                findings,
                occupied,
                "private_name",
                match.start(),
                match.end(),
            )
    return tuple(sorted(findings, key=lambda item: (item.start, item.end, item.kind)))


def redact_text(
    text: str,
    *,
    private_names: Iterable[str] = (),
) -> tuple[str, tuple[PrivacyFinding, ...]]:
    findings = scan_text(text, private_names=private_names)
    if not findings:
        return text, ()
    output: list[str] = []
    cursor = 0
    for finding in findings:
        if finding.start < cursor:
            continue
        output.append(text[cursor : finding.start])
        output.append(f"[redacted:{finding.kind}]")
        cursor = finding.end
    output.append(text[cursor:])
    return "".join(output), findings


def privacy_check(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_dir():
        raise ValueError("preview directory does not exist")
    report_path = path / "privacy-report.json"
    if not report_path.exists():
        raise ValueError("privacy-report.json is missing")
    report = json.loads(report_path.read_text(encoding="utf-8-sig"))
    required = {
        "local_only",
        "raw_logs_contain_text",
        "preview_pseudonymized",
        "external_services_called",
        "findings",
    }
    errors: list[str] = []
    if not required.issubset(report):
        errors.append("privacy_report_incomplete")
    if report.get("local_only") is not True:
        errors.append("not_local_only")
    if report.get("raw_logs_contain_text") is not False:
        errors.append("raw_text_logging_not_disabled")
    if report.get("preview_pseudonymized") is not True:
        errors.append("preview_not_pseudonymized")
    if report.get("external_services_called"):
        errors.append("external_service_use_detected")
    fingerprint_path = path / "preview-fingerprint.txt"
    if not fingerprint_path.is_file() or not fingerprint_path.read_text(
        encoding="utf-8-sig"
    ).strip():
        errors.append("preview_fingerprint_missing")
    return {
        "path": str(path),
        "valid": not errors,
        "errors": errors,
        "findings": int(report.get("findings", 0)),
        "preview_pseudonymized": report.get("preview_pseudonymized") is True,
        "local_only": report.get("local_only") is True,
    }


def _append_finding(
    findings: list[PrivacyFinding],
    occupied: list[tuple[int, int]],
    kind: str,
    start: int,
    end: int,
) -> None:
    if any(start < existing_end and end > existing_start for existing_start, existing_end in occupied):
        return
    findings.append(PrivacyFinding(kind=kind, start=start, end=end))
    occupied.append((start, end))


def _looks_like_card(value: str) -> bool:
    digits = "".join(character for character in value if character.isdigit())
    if not 13 <= len(digits) <= 19 or len(set(digits)) == 1:
        return False
    total = 0
    parity = len(digits) % 2
    for index, character in enumerate(digits):
        digit = int(character)
        if index % 2 == parity:
            digit *= 2
            if digit > 9:
                digit -= 9
        total += digit
    return total % 10 == 0


def _url_has_secret(value: str) -> bool:
    try:
        query = parse_qsl(urlsplit(value).query, keep_blank_values=True)
    except ValueError:
        return False
    return any(key.casefold() in _SECRET_QUERY_KEYS and bool(item) for key, item in query)


def redact_url_secrets(value: str) -> str:
    """Redact sensitive URL values without sending or resolving the URL."""
    parts = urlsplit(value)
    query = [
        (key, "[redacted]" if key.casefold() in _SECRET_QUERY_KEYS else item)
        for key, item in parse_qsl(parts.query, keep_blank_values=True)
    ]
    return urlunsplit(
        (parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment)
    )
