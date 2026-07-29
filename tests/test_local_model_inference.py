from __future__ import annotations

import argparse
import asyncio
import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

import pytest

from conversation_agent.local_slm.cli import _doctor_async, _simulate
from conversation_agent.local_slm.context import LocalContextBuilder
from conversation_agent.local_slm.models import DialoguePolicyInput, GenerationRequest
from conversation_agent.local_slm.policy import RuleBasedDialoguePolicy
from conversation_agent.local_slm.provider import LocalModelError, OpenAICompatibleLocalProvider
from conversation_agent.local_slm.runtime_config import LocalLLMConfig
from conversation_agent.local_slm.validator import OutputValidator


class _MockHandler(BaseHTTPRequestHandler):
    server: _MockServer

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send(200, {"status": "ok"})
            return
        if self.path == "/v1/models":
            self._send(200, {"data": [{"id": self.server.model_id}]})
            return
        self._send(404, {"error": "not found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        self.server.requests.append(
            {"path": self.path, "payload": payload, "authorization": self.headers.get("Authorization")}
        )
        status, response = self.server.responses.pop(0)
        self._send(status, response)

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _MockServer(ThreadingHTTPServer):
    model_id: str
    responses: list[tuple[int, dict[str, Any]]]
    requests: list[dict[str, Any]]


@contextmanager
def _mock_server(
    responses: list[tuple[int, dict[str, Any]]],
    *,
    model_id: str = "Qwen/Qwen3-0.6B-GGUF:Q8_0",
) -> Iterator[_MockServer]:
    server = _MockServer(("127.0.0.1", 0), _MockHandler)
    server.model_id = model_id
    server.responses = list(responses)
    server.requests = []
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _completion(content: str, *, reasoning_content: str | None = None) -> dict[str, Any]:
    message: dict[str, Any] = {"content": content}
    if reasoning_content is not None:
        message["reasoning_content"] = reasoning_content
    return {
        "choices": [{"message": message}],
        "usage": {"prompt_tokens": 40, "completion_tokens": 12, "total_tokens": 52},
    }


def _valid_json(message: str = "Привет! Чем помочь?") -> str:
    return json.dumps(
        {
            "action": "reply",
            "messages": [message],
            "reaction": None,
            "handoff_required": False,
            "confidence": 0.9,
        },
        ensure_ascii=False,
    )


def _request() -> GenerationRequest:
    decision = RuleBasedDialoguePolicy().decide(DialoguePolicyInput(messages=("нужен бот",)))
    context = LocalContextBuilder().build(
        agent_id="test-agent",
        decision=decision,
        messages=[{"role": "user", "content": "нужен бот"}],
    )
    return GenerationRequest(policy=decision, context=context)


def _provider(server: _MockServer) -> OpenAICompatibleLocalProvider:
    return OpenAICompatibleLocalProvider(
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        model=server.model_id,
        timeout_seconds=1,
    )


def test_real_provider_sends_schema_no_think_and_parses_metrics() -> None:
    with _mock_server([(200, _completion(_valid_json()))]) as server:
        result = asyncio.run(_provider(server).generate(_request()))

    sent = server.requests[0]
    assert sent["path"] == "/v1/chat/completions"
    assert sent["payload"]["response_format"]["type"] == "json_schema"
    assert sent["payload"]["chat_template_kwargs"]["enable_thinking"] is False
    assert sent["payload"]["messages"][0]["content"].startswith("/no_think")
    assert sent["authorization"] == "Bearer local-no-key"
    assert result.backend == "llama.cpp"
    assert result.model == server.model_id
    assert result.completion_tokens == 12
    assert result.retry_count == 0


def test_invalid_json_gets_one_local_repair_retry() -> None:
    with _mock_server(
        [
            (200, _completion("not json")),
            (200, _completion(_valid_json("исправлено"))),
        ]
    ) as server:
        result = asyncio.run(_provider(server).generate(_request()))

    assert len(server.requests) == 2
    assert "Previous invalid output" in server.requests[1]["payload"]["messages"][1]["content"]
    assert result.messages == ("исправлено",)
    assert result.retry_count == 1


def test_reasoning_content_is_rejected_after_one_retry() -> None:
    response = _completion(_valid_json(), reasoning_content="hidden chain")
    with (
        _mock_server([(200, response), (200, response)]) as server,
        pytest.raises(LocalModelError, match="reasoning_output_detected"),
    ):
        asyncio.run(_provider(server).generate(_request()))

    assert len(server.requests) == 2


def test_schema_rejection_falls_back_to_json_object() -> None:
    with _mock_server(
        [
            (400, {"error": "response_format json_schema unsupported"}),
            (200, _completion(_valid_json())),
        ]
    ) as server:
        result = asyncio.run(_provider(server).generate(_request()))

    assert result.action == "reply"
    assert server.requests[1]["payload"]["response_format"] == {"type": "json_object"}


def test_local_simulate_never_switches_to_fake_implicitly(monkeypatch: pytest.MonkeyPatch) -> None:
    with _mock_server([(503, {"error": "down"})]) as server:
        monkeypatch.setenv("LOCAL_LLM_BASE_URL", f"http://127.0.0.1:{server.server_port}/v1")
        monkeypatch.setenv("LOCAL_LLM_TIMEOUT_SECONDS", "1")
        args = argparse.Namespace(
            contact_id="contact",
            agent_id="agent",
            message=["нужен бот"],
            fake=False,
        )
        with pytest.raises(LocalModelError, match="local_http_error:503"):
            _simulate(args)

    assert len(server.requests) == 1


def test_fake_provider_requires_explicit_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LOCAL_LLM_BASE_URL", "http://127.0.0.1:1/v1")
    result = _simulate(
        argparse.Namespace(
            contact_id="contact",
            agent_id="agent",
            message=["нужен бот"],
            fake=True,
        )
    )

    assert result["fake_provider"] is True
    assert result["backend"] == "fake"
    assert result["openai_fallback_used"] is False


def test_doctor_reports_model_mismatch_without_completion() -> None:
    with _mock_server([], model_id="other-model") as server:
        config = LocalLLMConfig(
            base_url=f"http://127.0.0.1:{server.server_port}/v1",
            model="Qwen/Qwen3-0.6B-GGUF:Q8_0",
        )
        result = asyncio.run(_doctor_async(config))

    assert result["Ready"] == "NO"
    assert "model mismatch" in result["Reason"]
    assert server.requests == []


def test_raw_and_normalized_outputs_remain_separate() -> None:
    raw = _valid_json("  коротко  ")
    with _mock_server([(200, _completion(raw))]) as server:
        generated = asyncio.run(_provider(server).generate(_request()))
    validation = OutputValidator().validate(generated)

    assert generated.raw_output == raw
    assert validation.normalized is not None
    assert validation.normalized.messages == ("коротко",)


def test_runtime_model_files_are_gitignored() -> None:
    gitignore = Path(".gitignore").read_text(encoding="utf-8")
    assert ".runtime/" in gitignore
