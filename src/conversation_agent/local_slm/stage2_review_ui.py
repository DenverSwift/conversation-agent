"""Local browser UI for blind Stage 2 human review."""

from __future__ import annotations

import json
import threading
import webbrowser
from dataclasses import dataclass, field
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from conversation_agent.local_slm.stage2_review import (
    BlindPair,
    build_blind_pairs,
    save_human_review,
)

ASSET_PACKAGE = "conversation_agent.local_slm"
ASSET_PATHS = {
    "/": ("assets/stage2_review.html", "text/html; charset=utf-8"),
    "/app.css": ("assets/stage2_review.css", "text/css; charset=utf-8"),
    "/app.js": ("assets/stage2_review.js", "text/javascript; charset=utf-8"),
}
MAX_REQUEST_BYTES = 64 * 1024


@dataclass
class ReviewUIState:
    run_dir: Path
    reviewer: str
    seed: int
    category: str | None = None
    pairs: list[BlindPair] = field(init=False)
    _pairs_by_id: dict[str, BlindPair] = field(init=False)
    _lock: threading.Lock = field(init=False)

    def __post_init__(self) -> None:
        self.pairs = build_blind_pairs(
            self.run_dir,
            seed=self.seed,
            category=self.category,
        )
        self._pairs_by_id = {pair.pair_id: pair for pair in self.pairs}
        self._lock = threading.Lock()

    @property
    def review_dir(self) -> Path:
        return self.run_dir / "reviews" / self.reviewer

    def snapshot(self) -> dict[str, Any]:
        items = []
        for pair in self.pairs:
            review = self._load_review(pair)
            items.append(
                {
                    **pair.payload,
                    "reviewed": review is not None,
                    "ratings": _blind_review_values(review),
                }
            )
        return {
            "reviewer": self.reviewer,
            "seed": self.seed,
            "category": self.category,
            "total": len(items),
            "reviewed": sum(bool(item["reviewed"]) for item in items),
            "items": items,
        }

    def save(self, pair_id: str, ratings: dict[str, Any]) -> dict[str, Any]:
        pair = self._pairs_by_id.get(pair_id)
        if pair is None:
            raise KeyError(f"unknown pair_id: {pair_id}")
        with self._lock:
            existing = self._load_review(pair)
            ratings = dict(ratings)
            ratings["note"] = str((existing or {}).get("note", ""))
            path = save_human_review(
                run_dir=self.run_dir,
                reviewer=self.reviewer,
                pair=pair,
                ratings=ratings,
            )
        snapshot = self.snapshot()
        return {
            "saved": True,
            "pair_id": pair_id,
            "reviewed": snapshot["reviewed"],
            "total": snapshot["total"],
            "path": str(path.relative_to(self.run_dir)),
        }

    def _load_review(self, pair: BlindPair) -> dict[str, Any] | None:
        path = self.review_dir / f"{pair.pair_id}.json"
        if not path.is_file():
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None


class ReviewUIRequestHandler(BaseHTTPRequestHandler):
    server_version = "ConversationAgentReview/1"

    def __init__(
        self,
        *args: Any,
        review_state: ReviewUIState,
        **kwargs: Any,
    ) -> None:
        self.review_state = review_state
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/api/review":
            self._send_json(HTTPStatus.OK, self.review_state.snapshot())
            return
        if path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return
        if path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        asset = ASSET_PATHS.get(path)
        if asset is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        asset_path, content_type = asset
        body = files(ASSET_PACKAGE).joinpath(asset_path).read_bytes()
        self._send_bytes(HTTPStatus.OK, body, content_type)

    def do_POST(self) -> None:
        if urlparse(self.path).path != "/api/reviews":
            self._send_json(HTTPStatus.NOT_FOUND, {"error": "not_found"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_REQUEST_BYTES:
                raise ValueError("invalid request size")
            value = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(value, dict):
                raise TypeError("request body must be an object")
            pair_id = value.get("pair_id")
            ratings = value.get("ratings")
            if not isinstance(pair_id, str) or not isinstance(ratings, dict):
                raise TypeError("pair_id and ratings are required")
            result = self.review_state.save(pair_id, ratings)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._send_json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
            return
        self._send_json(HTTPStatus.OK, result)

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _send_json(self, status: HTTPStatus, value: dict[str, Any]) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self._send_bytes(status, body, "application/json; charset=utf-8")

    def _send_bytes(
        self,
        status: HTTPStatus,
        body: bytes,
        content_type: str,
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; connect-src 'self'; img-src 'self' data:; "
            "script-src 'self'; style-src 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)


class ReviewUIServer(ThreadingHTTPServer):
    allow_reuse_address = False
    daemon_threads = True


def run_review_ui(
    *,
    run_dir: Path,
    reviewer: str,
    seed: int,
    category: str | None = None,
    port: int = 8765,
    open_browser: bool = True,
) -> dict[str, Any]:
    if not 1 <= port <= 65535:
        raise ValueError("port must be between 1 and 65535")
    state = ReviewUIState(
        run_dir=run_dir,
        reviewer=reviewer,
        seed=seed,
        category=category,
    )
    if not state.pairs:
        raise ValueError("no complete blind review pairs found")
    handler = partial(ReviewUIRequestHandler, review_state=state)
    server = ReviewUIServer(("127.0.0.1", port), handler)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"Stage 2 review UI: {url}")
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


def _blind_review_values(review: dict[str, Any] | None) -> dict[str, Any] | None:
    if review is None:
        return None
    return {
        "winner": review.get("winner"),
        "candidate_A": review.get("candidate_A"),
        "candidate_B": review.get("candidate_B"),
    }
