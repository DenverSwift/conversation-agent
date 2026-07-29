# Local Telegram SLM Stage 1 Results

Date: 2026-07-29

## Result

Stage 1 ran a real compact model through the complete local path:

```text
local-simulate
  -> OpenAICompatibleLocalProvider
  -> llama.cpp HTTP server
  -> Qwen3-0.6B Q8_0
  -> strict JSON Schema
  -> local validation
  -> Telegram message bubbles
```

No OpenAI request and no fake provider were used in the verified run.

## Runtime

- llama.cpp release: `b10173`
- llama.cpp source commit: `e9fa078`
- Build: GNU 13.2.0, Windows AMD64, CPU-only
- Model: `Qwen/Qwen3-0.6B-GGUF`
- Quantization: `Q8_0`
- Model file size: 639,446,688 bytes
- Context: 4096 tokens
- Maximum output: 256 tokens
- Endpoint: `http://127.0.0.1:8080/v1`
- Thinking: disabled with `/no_think` and `enable_thinking=false`
- CPU: AMD Ryzen 7 260 with Radeon 780M Graphics, 8 cores / 16 threads
- Available GPUs: AMD Radeon 780M and NVIDIA GeForce RTX 5060 Laptop GPU
- GPU used for this verification: no
- Observed server working set: about 1.36 GB
- Observed private memory: about 771 MB

`winget install ggml.llamacpp` installed the official Vulkan package, but
Windows Code Integrity rejected the downloaded executable with
`0xC0E90002`. The installer therefore exercised its official-source fallback
and built the same release locally as CPU-only. No Windows security setting
was disabled.

## Commands Verified

```powershell
scripts\local_slm\install_llama_cpp.bat
scripts\local_slm\start_qwen3_06b.bat
scripts\local_slm\check_qwen3_06b.bat
python -m conversation_agent local-model-doctor
python -m conversation_agent local-simulate --contact-id live-stage1 `
  --agent-id informal-manager --message "привет" --message "нужен бот для заявок"
python -m conversation_agent local-model-smoke
scripts\local_slm\stop_qwen3_06b.bat
```

The direct check returned model ID
`Qwen/Qwen3-0.6B-GGUF:Q8_0` and a minimal JSON completion in 654 ms.

The doctor returned:

```text
Local model server: OK
Chat completions: OK
Structured output: OK
Non-thinking: OK
OpenAI fallback: disabled
OpenAI key used: false
Ready: YES
```

The verified `local-simulate` result used:

```text
provider: local_openai_compatible
backend: llama.cpp
model: Qwen/Qwen3-0.6B-GGUF:Q8_0
fake_provider: false
openai_fallback_used: false
validator.valid: true
```

That run took 5,997 ms for 51 completion tokens, or about 8.5 tokens/second.
TTFT is unavailable because Stage 1 is non-streaming.

## Smoke Results

Report: `.runtime/local_slm/smoke/20260729-185832/report.json`

- Scenarios: 5
- Valid structured outputs: 5
- Validation success rate: 100%
- Actions covered: `reply`, `no_reply`, `handoff`, `reaction`
- Observed total latency: 3.6 to 9.7 seconds
- Observed throughput: 8.3 to 10.7 completion tokens/second
- Reasoning leakage: none
- OpenAI fallback: none
- Fake provider: none

The 0.6B model obeyed the constrained structure, but reply quality remained
uneven: it sometimes repeated the incoming text or produced more bubbles than
a human operator would choose. This stage proves local execution and contract
enforcement, not production response quality.

## Automated Checks

```text
pytest: 118 passed
ruff: passed
pyright: passed
```

Tests cover the real provider request shape with a mock HTTP server, JSON
Schema and JSON object fallback, `/no_think`, reasoning rejection, one repair
retry, timeout/unavailable behavior, model mismatch, explicit fake mode,
raw/normalized separation, no implicit OpenAI fallback, and runtime ignore
rules.

## Repository Safety

- The model and Hugging Face cache are outside Git.
- llama.cpp source/build output, PID files, logs, and smoke reports are under
  ignored `.runtime/`.
- No secrets or OpenAI API key are required by the local path.
- The normal project default remains `GENERATION_MODE=openai_only`.
- The base branch and `main` are not modified by this experiment branch.
