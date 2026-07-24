"""Private SQLite state for successful incremental style compilations."""

from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from conversation_agent.style.models import StyleExample, StyleRule

STATE_SCHEMA_VERSION = "2"

SCHEMA = """
CREATE TABLE metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE source_analysis (
    source_key TEXT PRIMARY KEY,
    content_hash TEXT NOT NULL,
    source_type TEXT NOT NULL,
    contact_id INTEGER NOT NULL,
    analysis_status TEXT NOT NULL,
    observations_json TEXT NOT NULL,
    evidence_contribution_ids_json TEXT NOT NULL,
    compiled_at TEXT NOT NULL,
    bundle_id TEXT NOT NULL,
    example_json TEXT NOT NULL
);
CREATE INDEX source_analysis_content_hash_idx
    ON source_analysis(content_hash);
CREATE TABLE content_analysis (
    content_hash TEXT PRIMARY KEY,
    observations_json TEXT NOT NULL,
    analysis_fingerprint TEXT NOT NULL,
    compiled_at TEXT NOT NULL
);
CREATE TABLE bundle_artifacts (
    relative_path TEXT PRIMARY KEY,
    content BLOB NOT NULL
);
"""


@dataclass(frozen=True)
class CachedSource:
    example: StyleExample
    observations: tuple[StyleRule, ...]
    compiled_at: str
    bundle_id: str


@dataclass(frozen=True)
class CompilerState:
    metadata: dict[str, str]
    sources: dict[str, CachedSource]
    content_cache: dict[str, tuple[StyleRule, ...]]
    artifacts: dict[str, bytes]


def load_compiler_state(path: Path) -> CompilerState | None:
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path)) as connection:
        connection.row_factory = sqlite3.Row
        metadata = {
            str(row["key"]): str(row["value"])
            for row in connection.execute("SELECT key, value FROM metadata")
        }
        if metadata.get("state_schema_version") != STATE_SCHEMA_VERSION:
            raise ValueError(
                "Unsupported style compiler state schema. "
                "Run build_style_bundle with --full-rebuild."
            )
        sources: dict[str, CachedSource] = {}
        for row in connection.execute("SELECT * FROM source_analysis"):
            example = StyleExample.from_dict(json.loads(str(row["example_json"])))
            sources[str(row["source_key"])] = CachedSource(
                example=example,
                observations=_rules(str(row["observations_json"])),
                compiled_at=str(row["compiled_at"]),
                bundle_id=str(row["bundle_id"]),
            )
        content_cache = {
            str(row["content_hash"]): _rules(str(row["observations_json"]))
            for row in connection.execute("SELECT * FROM content_analysis")
        }
        artifacts = {
            str(row["relative_path"]): bytes(row["content"])
            for row in connection.execute("SELECT * FROM bundle_artifacts")
        }
    return CompilerState(metadata, sources, content_cache, artifacts)


def write_compiler_state(
    path: Path,
    *,
    metadata: dict[str, str],
    sources: dict[str, CachedSource],
    content_cache: dict[str, tuple[StyleRule, ...]],
    artifacts: dict[str, bytes],
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    with closing(sqlite3.connect(path)) as connection:
        connection.executescript(SCHEMA)
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            sorted(metadata.items()),
        )
        for source_key, cached in sorted(sources.items()):
            observations_json = _rules_json(cached.observations)
            connection.execute(
                """
                INSERT INTO source_analysis(
                    source_key, content_hash, source_type, contact_id,
                    analysis_status, observations_json,
                    evidence_contribution_ids_json, compiled_at, bundle_id,
                    example_json
                ) VALUES (?, ?, ?, ?, 'compiled', ?, ?, ?, ?, ?)
                """,
                (
                    source_key,
                    cached.example.content_hash,
                    cached.example.source_type,
                    cached.example.contact_id,
                    observations_json,
                    json.dumps(
                        [item.observation_id for item in cached.observations],
                        sort_keys=True,
                    ),
                    cached.compiled_at,
                    cached.bundle_id,
                    json.dumps(
                        cached.example.to_dict(),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )
        for content_hash, observations in sorted(content_cache.items()):
            connection.execute(
                """
                INSERT INTO content_analysis(
                    content_hash, observations_json,
                    analysis_fingerprint, compiled_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    content_hash,
                    _rules_json(observations),
                    metadata["analysis_fingerprint"],
                    metadata["last_successful_build"],
                ),
            )
        connection.executemany(
            "INSERT INTO bundle_artifacts(relative_path, content) VALUES (?, ?)",
            sorted(artifacts.items()),
        )
        connection.commit()


def load_compiler_artifacts(path: Path) -> dict[str, bytes]:
    state = load_compiler_state(path)
    return state.artifacts if state is not None else {}


def update_last_build_mode(path: Path, mode: str) -> None:
    if not path.is_file():
        return
    with closing(sqlite3.connect(path)) as connection:
        connection.execute(
            """
            INSERT INTO metadata(key, value) VALUES ('last_build_mode', ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
            """,
            (mode,),
        )
        connection.commit()


def _rules(value: str) -> tuple[StyleRule, ...]:
    parsed = json.loads(value)
    return tuple(StyleRule.from_dict(item) for item in parsed)


def _rules_json(rules: tuple[StyleRule, ...]) -> str:
    return json.dumps(
        [rule.to_dict() for rule in rules],
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
