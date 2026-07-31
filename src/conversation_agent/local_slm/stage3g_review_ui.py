"""Localhost-only blind review UI for Stage 3G."""

from __future__ import annotations

import json
import threading
import webbrowser
from dataclasses import dataclass, field
from datetime import UTC, datetime
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from conversation_agent.local_slm.stage2_dataset import atomic_write_json

ASSET_PACKAGE = "conversation_agent.local_slm"
ASSET_PATHS = {
    "/": ("assets/stage3g_review.html", "text/html; charset=utf-8"),
    "/app.css": ("assets/stage3g_review.css", "text/css; charset=utf-8"),
    "/app.js": ("assets/stage3g_review.js", "text/javascript; charset=utf-8"),
}
CHOICES = frozenset(
    {
        "a_much_better",
        "a_slightly_better",
        "tie",
        "b_slightly_better",
        "b_much_better",
        "both_bad",
        "skip",
    }
)
ISSUE_TAGS = frozenset(
    {
        "assistant-like",
        "semantically wrong",
        "invented information",
        "repeats incoming",
        "too long",
        "too short",
        "too many bubbles",
        "unnecessary question",
        "inappropriate profanity",
        "unnatural politeness",
        "wrong casing",
        "style mismatch",
        "privacy concern",
    }
)
MAX_REQUEST_BYTES = 32 * 1024


@dataclass
class Stage3GReviewState:
    run_dir: Path
    reviewer: str
    seed: int
    _lock: threading.Lock = field(init=False)
    _mapping: dict[str, Any] = field(init=False)
    _pairs: dict[str, dict[str, Any]] = field(init=False)

    def __post_init__(self) -> None:
        self._lock = threading.Lock()
        self._mapping = _load_json(self.run_dir / "blind-mapping.json")
        if int(self._mapping.get("seed", self.seed)) != self.seed:
            raise ValueError("blind mapping seed mismatch")
        self._pairs = self._load_pairs()
        if not self._pairs:
            raise ValueError("no complete Stage 3G blind pairs found")

    @property
    def review_dir(self) -> Path:
        return self.run_dir / "reviews" / self.reviewer

    def snapshot(self) -> dict[str, Any]:
        items = []
        for pair_id, pair in sorted(self._pairs.items()):
            review = self._review(pair_id)
            items.append(
                {
                    **pair,
                    "reviewed": review is not None,
                    "saved_choice": review.get("choice") if review else None,
                    "saved_issue_tags": review.get("issue_tags", []) if review else [],
                    "target_reveal_available": bool(
                        review and pair["track"] == "private-shadow"
                    ),
                }
            )
        return {
            "reviewer": self.reviewer,
            "seed": self.seed,
            "total": len(items),
            "reviewed": sum(bool(item["reviewed"]) for item in items),
            "remaining": sum(not item["reviewed"] for item in items),
            "tracks": {
                track: sum(item["track"] == track for item in items)
                for track in ("controlled", "private-shadow")
            },
            "items": items,
        }

    def save(
        self, pair_id: str, choice: str, issue_tags: list[str]
    ) -> dict[str, Any]:
        if pair_id not in self._pairs:
            raise KeyError(f"unknown pair_id: {pair_id}")
        if choice not in CHOICES:
            raise ValueError("invalid review choice")
        if any(tag not in ISSUE_TAGS for tag in issue_tags):
            raise ValueError("invalid issue tag")
        value = {
            "schema_version": 1,
            "pair_id": pair_id,
            "reviewer": self.reviewer,
            "choice": choice,
            "issue_tags": sorted(set(issue_tags)),
            "saved_at": datetime.now(UTC).isoformat(),
        }
        with self._lock:
            atomic_write_json(self.review_dir / f"{pair_id}.json", value)
        snapshot = self.snapshot()
        return {
            "saved": True,
            "pair_id": pair_id,
            "reviewed": snapshot["reviewed"],
            "total": snapshot["total"],
        }

    def reveal_target(self, pair_id: str) -> dict[str, Any]:
        pair = self._pairs.get(pair_id)
        if pair is None:
            raise KeyError(f"unknown pair_id: {pair_id}")
        if pair["track"] != "private-shadow":
            raise ValueError("target reveal is private-track only")
        if self._review(pair_id) is None:
            raise PermissionError("rate the pair before revealing the target")
        hidden = _load_json(self.run_dir / "hidden-targets.json")
        value = hidden.get(pair_id)
        if not isinstance(value, dict):
            raise KeyError("hidden target unavailable")
        return {"pair_id": pair_id, "messages": list(value.get("messages", []))}

    def _review(self, pair_id: str) -> dict[str, Any] | None:
        path = self.review_dir / f"{pair_id}.json"
        return _load_json(path) if path.is_file() else None

    def _load_pairs(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        mappings = self._mapping.get("pairs", {})
        for pair_id, display in mappings.items():
            if not isinstance(display, dict):
                continue
            track = (
                "private-shadow"
                if str(pair_id).startswith("private__")
                else "controlled"
            )
            identifier = str(pair_id).split("__", 1)[-1]
            records = {
                variant: _load_optional_json(
                    self.run_dir / track / identifier / f"{variant}.json"
                )
                for variant in ("N", "R")
            }
            if any(value is None or value.get("provider_error") for value in records.values()):
                continue
            first = records["N"] or {}
            result[str(pair_id)] = {
                "pair_id": str(pair_id),
                "track": track,
                "category": str(first.get("metadata", {}).get("category", "unknown")),
                "context": _public_context(first),
                "candidate_A": _public_candidate(records[str(display["A"])] or {}),
                "candidate_B": _public_candidate(records[str(display["B"])] or {}),
            }
        return result


class Stage3GReviewHandler(BaseHTTPRequestHandler):
    server_version = "ConversationAgentStage3GReview/1"

    def __init__(
        self,
        *args: Any,
        review_state: Stage3GReviewState,
        **kwargs: Any,
    ) -> None:
        self.review_state = review_state
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/review":
            self._send_json(HTTPStatus.OK, self.review_state.snapshot())
            return
        if parsed.path == "/api/target":
            pair_id = parse_qs(parsed.query).get("pair_id", [""])[0]
            try:
                value = self.review_state.reveal_target(pair_id)
            except PermissionError as exc:
                self._send_json(HTTPStatus.FORBIDDEN, {"error": str(exc)})
                return
            except (KeyError, ValueError) as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
                return
            self._send_json(HTTPStatus.OK, value)
            return
        if parsed.path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if parsed.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        asset = ASSET_PATHS.get(parsed.path)
        if asset is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        asset_path, content_type = asset
        self._send_bytes(
            HTTPStatus.OK,
            files(ASSET_PACKAGE).joinpath(asset_path).read_bytes(),
            content_type,
        )

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/reviews":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= MAX_REQUEST_BYTES:
                raise ValueError("invalid request size")
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(value, dict):
                raise TypeError("request body must be an object")
            pair_id = value.get("pair_id")
            choice = value.get("choice")
            issue_tags = value.get("issue_tags", [])
            if (
                not isinstance(pair_id, str)
                or not isinstance(choice, str)
                or not isinstance(issue_tags, list)
                or not all(isinstance(item, str) for item in issue_tags)
            ):
                raise TypeError("pair_id, choice and issue_tags are required")
            result = self.review_state.save(pair_id, choice, issue_tags)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, result)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: HTTPStatus, value: dict[str, Any]) -> None:
        self._send_bytes(
            status,
            json.dumps(value, ensure_ascii=False).encode("utf-8"),
            "application/json; charset=utf-8",
        )

    def _send_bytes(
        self, status: HTTPStatus, body: bytes, content_type: str
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "script-src 'self'; style-src 'self'; base-uri 'none'; "
            "frame-ancestors 'none'; form-action 'self'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)


class Stage3GReviewServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True


def run_stage3g_review_ui(
    *,
    run_dir: Path,
    reviewer: str,
    seed: int,
    port: int = 8766,
    open_browser: bool = True,
) -> dict[str, Any]:
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    state = Stage3GReviewState(run_dir=run_dir, reviewer=reviewer, seed=seed)
    handler = partial(Stage3GReviewHandler, review_state=state)
    server = Stage3GReviewServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Stage 3G blind review UI: {url}")
    print("Press Ctrl+C to stop.")
    if open_browser:
        threading.Timer(0.25, webbrowser.open, args=(url,)).start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    snapshot = state.snapshot()
    return {
        "url": url,
        "reviewer": reviewer,
        "reviewed": snapshot["reviewed"],
        "total": snapshot["total"],
        "stopped": True,
    }


def _public_context(record: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = record.get("metadata", {})
    if record.get("track") == "private-shadow":
        episode = metadata.get("public_episode", {})
        return list(episode.get("preceding_context", [])) + list(
            episode.get("latest_incoming", [])
        )
    scenario = metadata.get("scenario", {})
    turns: list[dict[str, Any]] = []
    for group in scenario.get("conversation", []):
        role = group.get("role", "contact")
        turns.extend(
            {"role": role, "content": message}
            for message in group.get("messages", [])
        )
    return turns


def _public_candidate(record: dict[str, Any]) -> dict[str, Any]:
    output = record.get("normalized_output", {})
    return {
        "action": output.get("action"),
        "messages": list(output.get("messages", [])),
        "reaction": output.get("reaction"),
        "handoff_required": bool(output.get("handoff_required")),
    }


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise TypeError(f"{path} must contain an object")
    return value


def _load_optional_json(path: Path) -> dict[str, Any] | None:
    return _load_json(path) if path.is_file() else None
