"""Read-only discovery and inspection of local provenance sources."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SQLITE_SUFFIXES = frozenset({".sqlite", ".sqlite3", ".db"})
ALLOWED_SEARCH_DIRECTORIES = (
    ".runtime",
    "runtime",
    "data",
    "logs",
    "exports",
)
IGNORED_DIRECTORY_NAMES = frozenset(
    {".git", ".venv", "venv", "__pycache__", "node_modules"}
)


class ProvenanceDiscoveryError(ValueError):
    """Raised when local provenance source discovery cannot proceed safely."""


@dataclass(frozen=True)
class DiscoveryRoot:
    alias: str
    path: Path
    kind: str
    branch: str | None = None


@dataclass(frozen=True)
class TableInspection:
    name: str
    columns: tuple[str, ...]
    row_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "columns": list(self.columns),
        }


@dataclass(frozen=True)
class DatabaseInspection:
    alias: str
    root_alias: str
    relative_path: str
    file_size: int
    modified_at: str
    sqlite_valid: bool
    schema_classification: str
    tables: tuple[TableInspection, ...]
    has_telegram_message_ids: bool
    has_chat_or_contact_ids: bool
    has_text_hashes: bool
    has_provider_or_model_markers: bool
    has_review_status: bool
    date_range: dict[str, str | None]
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "tables": [item.to_dict() for item in self.tables],
        }


def discover_provenance_sources(
    *,
    repo_root: Path,
    output: Path,
    include_git_worktrees: bool,
    extra_roots: Iterable[Path] = (),
) -> dict[str, Any]:
    resolved_repo = repo_root.resolve()
    if not (resolved_repo / ".git").exists():
        raise ProvenanceDiscoveryError("repo root is not a Git worktree")
    roots = discover_roots(
        resolved_repo,
        include_git_worktrees=include_git_worktrees,
        extra_roots=extra_roots,
    )
    candidates: list[tuple[DiscoveryRoot, Path]] = []
    seen: set[Path] = set()
    for root in roots:
        for path in _candidate_database_paths(root.path):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            candidates.append((root, resolved))
    candidates.sort(key=lambda item: (item[0].alias, str(item[1]).casefold()))
    inspections: list[DatabaseInspection] = []
    location_map: dict[str, str] = {}
    for index, (root, path) in enumerate(candidates, start=1):
        alias = f"database-{index:03d}"
        location_map[alias] = str(path)
        inspections.append(
            inspect_sqlite_database(
                path,
                alias=alias,
                root_alias=root.alias,
                relative_path=_safe_relative(path, root.path),
            )
        )
    output.mkdir(parents=True, exist_ok=True)
    generated_at = datetime.now(UTC).isoformat()
    report = {
        "schema_version": 1,
        "generated_at": generated_at,
        "repo_alias": "current-repository",
        "scan_scope": "repository_worktrees_and_explicit_roots_only",
        "whole_disk_scanned": False,
        "roots": [
            {
                "alias": item.alias,
                "kind": item.kind,
                "branch": item.branch,
            }
            for item in roots
        ],
        "candidate_databases": len(inspections),
        "readable_databases": sum(item.sqlite_valid for item in inspections),
        "databases": [item.to_dict() for item in inspections],
    }
    _write_json(output / "discovery-report.json", report)
    _write_json(
        output / "locations.private.json",
        {
            "schema_version": 1,
            "local_only": True,
            "locations": location_map,
        },
    )
    resume = (
        "python -m conversation_agent dataset provenance-discover "
        f'--repo-root "{repo_root}" --include-git-worktrees '
        f'--output "{output}" --extra-root "<path>"'
    )
    (output / "README.md").write_text(
        "\n".join(
            (
                "# Local provenance discovery",
                "",
                "The scan was limited to repository worktrees and explicit project roots.",
                f"Candidate databases: {len(inspections)}",
                f"Readable databases: {sum(item.sqlite_valid for item in inspections)}",
                "",
                "Resume with an additional explicitly approved root:",
                "",
                "```powershell",
                resume,
                "```",
                "",
            )
        ),
        encoding="utf-8",
    )
    return {
        "output": str(output),
        "worktrees": sum(item.kind == "git_worktree" for item in roots),
        "candidate_databases": len(inspections),
        "readable_databases": sum(item.sqlite_valid for item in inspections),
        "whole_disk_scanned": False,
        "resume_command": resume,
    }


def discover_roots(
    repo_root: Path,
    *,
    include_git_worktrees: bool,
    extra_roots: Iterable[Path] = (),
) -> list[DiscoveryRoot]:
    roots: list[DiscoveryRoot] = []
    seen: set[Path] = set()
    if include_git_worktrees:
        for path, branch in git_worktrees(repo_root):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            roots.append(
                DiscoveryRoot(
                    alias=f"worktree-{len(roots) + 1:02d}",
                    path=resolved,
                    kind="git_worktree",
                    branch=branch,
                )
            )
    else:
        resolved = repo_root.resolve()
        seen.add(resolved)
        roots.append(
            DiscoveryRoot(
                alias="worktree-01",
                path=resolved,
                kind="current_repository",
            )
        )
    for extra in extra_roots:
        resolved = extra.resolve()
        if not resolved.exists() or not resolved.is_dir():
            raise ProvenanceDiscoveryError(f"extra root does not exist: {extra}")
        if resolved in seen:
            continue
        seen.add(resolved)
        roots.append(
            DiscoveryRoot(
                alias=f"extra-root-{sum(item.kind == 'extra_root' for item in roots) + 1:02d}",
                path=resolved,
                kind="extra_root",
            )
        )
    return roots


def git_worktrees(repo_root: Path) -> list[tuple[Path, str | None]]:
    result = subprocess.run(
        ["git", "-C", str(repo_root), "worktree", "list", "--porcelain"],
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    worktrees: list[tuple[Path, str | None]] = []
    path: Path | None = None
    branch: str | None = None
    for line in [*result.stdout.splitlines(), ""]:
        if line.startswith("worktree "):
            if path is not None:
                worktrees.append((path, branch))
            path = Path(line.removeprefix("worktree ").strip())
            branch = None
        elif line.startswith("branch "):
            branch = line.removeprefix("branch refs/heads/").strip()
        elif not line and path is not None:
            worktrees.append((path, branch))
            path = None
            branch = None
    return worktrees


def inspect_sqlite_database(
    path: Path,
    *,
    alias: str,
    root_alias: str,
    relative_path: str,
) -> DatabaseInspection:
    stat = path.stat()
    tables: list[TableInspection] = []
    date_min: str | None = None
    date_max: str | None = None
    try:
        with _readonly_connection(path) as connection:
            integrity = connection.execute("PRAGMA quick_check").fetchone()
            if integrity is None or str(integrity[0]).casefold() != "ok":
                raise sqlite3.DatabaseError("SQLite quick_check failed")
            names = [
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            for name in names:
                quoted = _quote_identifier(name)
                columns = tuple(
                    str(row[1])
                    for row in connection.execute(f"PRAGMA table_info({quoted})")
                )
                count = int(
                    connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()[0]
                )
                tables.append(
                    TableInspection(name=name, columns=columns, row_count=count)
                )
                date_columns = [
                    item
                    for item in columns
                    if any(
                        token in item.casefold()
                        for token in ("created_at", "sent_at", "timestamp", "updated_at")
                    )
                ]
                for column in date_columns[:2]:
                    quoted_column = _quote_identifier(column)
                    row = connection.execute(
                        f"SELECT MIN({quoted_column}), MAX({quoted_column}) "
                        f"FROM {quoted}"
                    ).fetchone()
                    date_min = _minimum_text(date_min, row[0])
                    date_max = _maximum_text(date_max, row[1])
    except (sqlite3.Error, OSError) as exc:
        return DatabaseInspection(
            alias=alias,
            root_alias=root_alias,
            relative_path=relative_path,
            file_size=stat.st_size,
            modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
            sqlite_valid=False,
            schema_classification="unreadable",
            tables=(),
            has_telegram_message_ids=False,
            has_chat_or_contact_ids=False,
            has_text_hashes=False,
            has_provider_or_model_markers=False,
            has_review_status=False,
            date_range={"from": None, "until": None},
            error=type(exc).__name__,
        )
    all_columns = {
        column.casefold() for table in tables for column in table.columns
    }
    return DatabaseInspection(
        alias=alias,
        root_alias=root_alias,
        relative_path=relative_path,
        file_size=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
        sqlite_valid=True,
        schema_classification=_classify_schema(tables),
        tables=tuple(tables),
        has_telegram_message_ids=bool(
            all_columns
            & {
                "sent_message_id",
                "telegram_message_id",
                "incoming_message_id",
                "message_id",
            }
        ),
        has_chat_or_contact_ids=bool(
            all_columns & {"dialog_id", "chat_id", "contact_id", "destination_id"}
        ),
        has_text_hashes=bool(
            all_columns & {"content_hash", "text_hash", "draft_hash"}
        ),
        has_provider_or_model_markers=bool(
            all_columns & {"provider", "model", "model_id", "prompt_version"}
        ),
        has_review_status=bool(
            all_columns
            & {
                "feedback_status",
                "approval_status",
                "status",
                "corrected_reply_text",
            }
        ),
        date_range={"from": date_min, "until": date_max},
    )


def load_discovered_locations(discovery: Path) -> dict[str, Path]:
    path = discovery / "locations.private.json"
    if not path.is_file():
        raise ProvenanceDiscoveryError("discovery location map is missing")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    locations = value.get("locations", {})
    if not isinstance(locations, dict):
        raise ProvenanceDiscoveryError("invalid discovery location map")
    return {str(alias): Path(str(location)) for alias, location in locations.items()}


def discovery_report(discovery: Path) -> dict[str, Any]:
    path = discovery / "discovery-report.json"
    if not path.is_file():
        raise ProvenanceDiscoveryError("discovery report is missing")
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ProvenanceDiscoveryError("invalid discovery report")
    return value


def _candidate_database_paths(root: Path) -> list[Path]:
    candidates: list[Path] = []
    for directory_name in ALLOWED_SEARCH_DIRECTORIES:
        directory = root / directory_name
        if not directory.is_dir():
            continue
        for path in directory.rglob("*"):
            if any(part in IGNORED_DIRECTORY_NAMES for part in path.parts):
                continue
            if path.is_file() and path.suffix.casefold() in SQLITE_SUFFIXES:
                candidates.append(path)
    configured = [
        os.environ.get("FEEDBACK_DATABASE_PATH"),
        os.environ.get("STYLE_COMPILER_STATE_PATH"),
    ]
    for raw_path in configured:
        if not raw_path:
            continue
        path = Path(raw_path)
        if not path.is_absolute():
            path = root / path
        if path.is_file() and path.suffix.casefold() in SQLITE_SUFFIXES:
            candidates.append(path)
    return candidates


def _readonly_connection(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(
        f"file:{path.resolve().as_posix()}?mode=ro",
        uri=True,
    )
    connection.row_factory = sqlite3.Row
    return connection


def readonly_connection(path: Path) -> sqlite3.Connection:
    """Public read-only connection helper used by reconciliation."""
    return _readonly_connection(path)


def _classify_schema(tables: Iterable[TableInspection]) -> str:
    names = {item.name.casefold() for item in tables}
    if "generated_replies" in names:
        return "feedback_and_send_audit"
    if "source_analysis" in names:
        return "style_compiler"
    if any("takeover" in item or "handoff" in item for item in names):
        return "human_takeover_audit"
    if any("trainer" in item or "approval" in item for item in names):
        return "trainer_audit"
    if any("message" in item or "send" in item for item in names):
        return "message_audit"
    return "generic_sqlite"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def quote_identifier(value: str) -> str:
    return _quote_identifier(value)


def _safe_relative(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        digest = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:12]
        return f"external/{digest}{path.suffix.casefold()}"


def _minimum_text(current: str | None, value: Any) -> str | None:
    parsed = str(value) if value is not None else None
    if not parsed:
        return current
    return parsed if current is None or parsed < current else current


def _maximum_text(current: str | None, value: Any) -> str | None:
    parsed = str(value) if value is not None else None
    if not parsed:
        return current
    return parsed if current is None or parsed > current else current


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)
