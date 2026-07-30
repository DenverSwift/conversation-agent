# Local Telegram SLM Experiment

This branch is an experimental proof of concept, not a production replacement
for the current OpenAI runtime.

## Goal

The experiment checks whether Telegram response generation can move from large
dynamic style prompts and repeated OpenAI calls to a compact local model path:

```mermaid
flowchart LR
  A["Telegram"] --> B["Message Buffer"]
  B --> C["Dialogue Policy"]
  C --> D["Context Builder"]
  D --> E["Local Generator"]
  E --> F["Validator"]
  F --> G["Approval"]
  G --> H["Telegram Behavior Runtime"]
  F --> I{"low confidence"}
  I --> J["OpenAI fallback"]
  I --> K["Human handoff"]
```

## Architecture

The proof of concept separates the system into small components:

- `DialoguePolicy`: chooses `reply`, `no_reply`, `wait`, `reaction`, or `handoff`.
- `LocalContextBuilder`: builds a short structured context instead of sending a
  full style bundle or long prompt.
- `LocalGenerationProvider`: supports fake offline generation and
  OpenAI-compatible local HTTP endpoints such as llama.cpp server or vLLM.
- `OutputValidator`: blocks invalid JSON shape, empty replies, duplicate
  bubbles, overly long bubbles, links, forbidden assistant phrases, stale drafts,
  takeover, pause, and missing approval.
- `HybridGenerationRouter`: supports `local_only`, `local_with_fallback`,
  `openai_only`, and `compare_shadow`.
- `AgentAdapterRegistry`: records active LoRA adapter metadata per agent.
- Dataset, training dry-run, and benchmark utilities live under
  `conversation_agent.local_slm`.

## Local Endpoint

Stage 1 uses the official llama.cpp HTTP server with
`Qwen/Qwen3-0.6B-GGUF:Q8_0`. Install and run it on localhost:

```powershell
scripts\local_slm\install_llama_cpp.bat
scripts\local_slm\start_qwen3_06b.bat
scripts\local_slm\check_qwen3_06b.bat
```

The start script binds only to `127.0.0.1:8080`, limits context to 4096
tokens, writes its PID and logs under `.runtime/local_slm/`, waits for
`/health` and `/v1/models`, and refuses to start a second server. Stop only
the managed process with:

```powershell
scripts\local_slm\stop_qwen3_06b.bat
```

Configuration:

```env
GENERATION_MODE=local_only
LOCAL_LLM_BASE_URL=http://127.0.0.1:8080/v1
LOCAL_LLM_MODEL=Qwen/Qwen3-0.6B-GGUF:Q8_0
LOCAL_LLM_API_KEY=local-no-key
LOCAL_LLM_TIMEOUT_SECONDS=30
LOCAL_LLM_MAX_OUTPUT_TOKENS=256
LOCAL_LLM_CONTEXT_TOKENS=4096
LOCAL_LLM_THINKING=false
```

The model is downloaded by llama.cpp into the user Hugging Face cache. It is
not stored in the repository. The normal project default remains
`GENERATION_MODE=openai_only`.

## Real Local Demo

First verify the full endpoint and provider contract:

```powershell
python -m conversation_agent local-model-doctor
```

Then run real local inference:

```powershell
python -m conversation_agent local-simulate `
  --contact-id test-contact `
  --agent-id informal-manager `
  --message "привет" `
  --message "нужен бот для заявок"
```

Without `--fake`, this command requires the real endpoint and fails clearly if
it is unavailable. It never switches to `FakeLocalGenerationProvider` or
OpenAI. The output includes provider, backend, model, accumulated messages,
policy, compact context, raw action, generated bubbles, validation, retry
count, tokens, latency, and tokens per second. TTFT is reported as unavailable
because Stage 1 uses non-streaming Chat Completions.

The fake provider remains available only when explicitly requested:

```powershell
python -m conversation_agent local-simulate --fake --message "нужен бот"
```

Run the five-scenario real smoke suite with:

```powershell
python -m conversation_agent local-model-smoke
```

Reports are written under `.runtime/local_slm/smoke/<timestamp>/`.

## Structured Output

The provider requests strict JSON Schema with the fields `action`, `messages`,
`reaction`, `handoff_required`, and `confidence`. The schema is constrained to
the action already selected by the dialogue policy. If an endpoint rejects
JSON Schema, the provider retries with JSON object mode and validates locally.

The Qwen chat template receives `enable_thinking=false`, and the system prompt
starts with `/no_think`. `<think>`, `</think>`, and `reasoning_content` make the
result invalid. Invalid JSON or semantically inconsistent fields receive one
local repair attempt; failure after that is surfaced without OpenAI fallback.

## Dataset

Build an SFT-style dataset from the reviewed local export:

```powershell
python -m conversation_agent build-slm-dataset `
  --source .runtime/exports/cleaned_examples.jsonl `
  --output .runtime/training/datasets/local-sft.jsonl
```

Rules:

- AI-generated messages are excluded from human targets.
- Duplicates are removed by deterministic fingerprint.
- Train/test split is deterministic by conversation ID.
- Private datasets stay under `.runtime/` and are ignored by Git.

## Training Dry-Run

Dry-run planning validates the dataset and estimates batches without loading a
model:

```powershell
python -m conversation_agent local-train-dry-run `
  --dataset .runtime/training/datasets/local-sft.jsonl `
  --base-model Qwen2.5-0.5B `
  --output-dir .runtime/models/adapters/informal-manager
```

Real LoRA/QLoRA training remains explicit and experimental. VRAM requirements
depend on the selected backend, quantization, sequence length, and model config;
the dry-run intentionally reports only planning estimates.

## Benchmark

Run the reproducible fake/local benchmark:

```powershell
python -m conversation_agent benchmark run `
  --dataset tests/fixtures/dialogue_benchmark.jsonl `
  --providers fake `
  --output .runtime/benchmarks/run-001
```

The benchmark writes raw outputs and summary metrics, including validity,
no-reply accuracy, output length, and provider failure rate. Blind comparison
can be layered on top of the saved candidate outputs without revealing provider
names before the operator decision.

## Stage 2 - Frozen Baseline Benchmark

Stage 2 records the quality of the untrained local model before any LoRA,
QLoRA, preference training, or private-data collection. The public benchmark
contains exactly 100 manually authored Russian dialogue scenarios under
`benchmarks/local_slm_stage2_v1/`.

The dataset is frozen by a deterministic fingerprint. Its manifest declares
`purpose=benchmark_only` and `allowed_for_training=false`; dataset builders and
training commands reject both this manifest and its registered fingerprint.
Changing the scenarios requires a new benchmark version.

Two modes answer different questions:

- `system_comparison` compares the current product pipelines: the local compact
  context and validator against the current OpenAI prompt/context path.
- `same_context` gives both models the same normalized semantic context,
  allowed actions, schema, and output-token limit. Provider chat templates
  still differ, so prompts are not claimed to be literally identical.

Start and verify the managed local server:

```powershell
scripts\local_slm\start_qwen3_06b.bat
python -m conversation_agent local-model-doctor
```

Run both real comparisons:

```powershell
uv run python -m conversation_agent benchmark stage2-run `
  --dataset benchmarks/local_slm_stage2_v1/scenarios.jsonl `
  --mode system_comparison `
  --providers local_qwen,openai_gpt4o_mini `
  --output .runtime/benchmarks/stage2-system-v1 `
  --seed 42

uv run python -m conversation_agent benchmark stage2-run `
  --dataset benchmarks/local_slm_stage2_v1/scenarios.jsonl `
  --mode same_context `
  --providers local_qwen,openai_gpt4o_mini `
  --output .runtime/benchmarks/stage2-same-context-v1 `
  --seed 42
```

Add `--resume` to either command to skip completed results. Add
`--retry-errors` with `--resume` to retry only failed provider results. A
successful OpenAI result is never called again during resume.

Run the blind review without revealing provider, model, latency, token counts,
or retry metadata:

```powershell
uv run python -m conversation_agent benchmark stage2-review-ui `
  --run .runtime/benchmarks/stage2-system-v1 `
  --reviewer denver `
  --seed 42
```

The local browser UI uses button-only controls. Human quality dimensions use a
three-point `bad / acceptable / good` scale stored as `1 / 3 / 5` for
compatibility with existing reports. Candidate presets fill all fields and can
then be adjusted individually. Reviews are saved to the same files as the CLI.

The terminal workflow remains available:

```powershell
uv run python -m conversation_agent benchmark stage2-review `
  --run .runtime/benchmarks/stage2-system-v1 `
  --reviewer denver `
  --seed 42 `
  --only-unreviewed
```

The CLI saves each rating immediately and supports `skip`, `back`, `progress`,
and category filtering. Reveal the deterministic A/B mapping separately:

```powershell
uv run python -m conversation_agent benchmark stage2-review `
  --run .runtime/benchmarks/stage2-system-v1 `
  --reviewer denver `
  --seed 42 `
  --reveal
```

Reveal does not modify saved ratings. Generate a report at any point:

```powershell
uv run python -m conversation_agent benchmark stage2-report `
  --run .runtime/benchmarks/stage2-system-v1 `
  --reviews .runtime/benchmarks/stage2-system-v1/reviews `
  --output .runtime/benchmarks/stage2-system-v1/report
```

Automatic metrics measure format, actions, factual constraints, output shape,
and latency; they do not establish a quality winner. Full human review is now
optional diagnostic tooling rather than a gate. A compact automatic diagnostic
pack must exist before the architecture experiment can proceed, and the user
still gives one qualitative conclusion before a final model choice.

## Stage 2.5 - Response Contract and Diagnostic Review

Stage 2 established that direct Qwen3-0.6B often chose the wrong action,
repeated incoming questions, and occasionally introduced unsupported details.
Direct GPT understood the dialogue much better but still tended to write too
much and to use too many Telegram bubbles. Requiring hundreds of manual scores
would not add enough information to justify blocking the next architecture
experiment.

Stage 2.5 separates decision-making from wording:

```mermaid
flowchart LR
  A["Conversation context"] --> B["GPT policy"]
  B --> C["ResponseContract"]
  C --> D["OpenAI renderer"]
  C --> E["Local Qwen renderer"]
  D --> F["Hard validator"]
  E --> F
  F --> G["Saved diagnostic results"]
```

`ResponseContract` fixes the action, goal, allowed and forbidden facts, target
and maximum bubble counts, total and per-bubble character limits, question
count, tone, relationship style, greeting and emoji permissions, reaction,
handoff state, and confidence before any final message is written.
`LengthPlanner` adapts these limits to the action, relationship, request
complexity, available facts, urgency, and conversation history. Benchmark
expected actions never enter the runtime policy prompt.

The hard validator checks action, bubbles, characters, questions, greetings,
emoji, required and forbidden facts, thinking text, assistant meta phrases,
headings, lists, repeated incoming questions, empty replies, reactions, and
handoffs. A renderer gets one repair attempt with the exact violations. The
same renderer performs that retry; OpenAI never repairs local output, and the
policy is not called again for a renderer failure.

The four comparison pipelines are:

- `openai_direct`: saved Stage 2 GPT baseline.
- `local_direct`: saved Stage 2 Qwen baseline.
- `gpt_policy_openai_renderer`: GPT policy plus OpenAI wording.
- `gpt_policy_local_renderer`: the same GPT contract plus local Qwen wording.

Install and verify the CUDA build without replacing the CPU build:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  scripts\local_slm\install_llama_cpp_cuda.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File `
  scripts\local_slm\start_qwen3_06b_cuda.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File `
  scripts\local_slm\check_gpu_offload.ps1
uv run python -m conversation_agent local-model-doctor
```

The installer downloads official llama.cpp Windows CUDA archives under
`.runtime/`, discovers the supported GPU-layer flag from `llama-server
--help`, and verifies `--list-devices`. The start and check scripts require
CUDA initialization, nonzero offloaded layers, a visible `llama-server`
process in `nvidia-smi`, increased VRAM use, healthy model endpoints, and a
real completion. `Ready: YES` is never based on `nvidia-smi` alone. CPU
inference remains available through the original scripts; a Stage 2.5 run only
allows it when `--allow-cpu` is explicitly supplied.

Run the architecture experiment:

```powershell
uv run python -m conversation_agent benchmark stage25-run `
  --dataset benchmarks/local_slm_stage2_v1/scenarios.jsonl `
  --pipelines openai_direct,local_direct,gpt_policy_openai_renderer,gpt_policy_local_renderer `
  --output .runtime/benchmarks/stage25-quick20 `
  --baseline .runtime/benchmarks/stage2-system-v1 `
  --seed 42 `
  --scenario-limit 20 `
  --gpu-required

uv run python -m conversation_agent benchmark stage25-run `
  --dataset benchmarks/local_slm_stage2_v1/scenarios.jsonl `
  --pipelines gpt_policy_openai_renderer,gpt_policy_local_renderer `
  --output .runtime/benchmarks/stage25-v1 `
  --baseline .runtime/benchmarks/stage2-system-v1 `
  --seed 42 `
  --gpu-required
```

The quick selector covers no-reply, handoff, reaction, incomplete requests,
hallucination risk, conflict, friendly and formal conversations,
multi-message bursts, and short acknowledgements. `--resume` skips completed
artifacts, while `--retry-errors` retries explicit provider, contract, or
renderer failures. `--no-openai` and `--no-local` remove dependent pipelines;
no fake provider is substituted.

Create compact diagnostics and the final report:

```powershell
uv run python -m conversation_agent benchmark diagnostic-pack `
  --run .runtime/benchmarks/stage2-system-v1 `
  --output .runtime/benchmarks/stage2-system-v1/diagnostic-pack `
  --max-examples 40 `
  --seed 42

uv run python -m conversation_agent benchmark stage25-report `
  --run .runtime/benchmarks/stage25-v1 `
  --baseline .runtime/benchmarks/stage2-system-v1 `
  --output .runtime/benchmarks/stage25-v1/report
```

The diagnostic pack is deterministic for a fixed seed, contains no duplicate
scenario IDs, labels providers openly, and selects representative error and
control examples. It asks for one short qualitative conclusion, not a rating
form for every scenario. The blind review CLI and browser UI remain available
when more diagnosis is useful.

`READY_FOR_ARCHITECTURE_EXPERIMENT` only means the frozen benchmark and
automatic artifacts are adequate for testing this decomposition. It does not
mean production-ready, autopilot-ready, selected for training, or approved by
the user. See
[Stage 2.5 results](local-telegram-slm-stage25-results.md) for the real CUDA
run and current technical recommendation.

## Fallback

OpenAI remains available as fallback, benchmark, and teacher. The production
default is still:

```env
GENERATION_MODE=openai_only
```

The experiment does not remove the OpenAI provider and does not switch the
production flow to local generation by default.

## Limits

- Qwen3 0.6B produces schema-valid output but response quality is still only
  experimental and can be repetitive or shallow.
- Stage 1 uses non-streaming Chat Completions, so TTFT is unavailable.
- The fake provider is only available through the explicit `--fake` flag.
- The real provider expects the managed local server or another compatible
  endpoint.
- In-process Hugging Face support is intentionally not imported by the base
  runtime; it belongs in an optional `training` or `local-inference` extra.
- No weights, adapters, private exports, or datasets should be committed.

See [Stage 1 results](local-telegram-slm-stage1-results.md) for the verified
machine, commands, timings, and remaining limitations.

See [Stage 2 results](local-telegram-slm-stage2-results.md) for the frozen
dataset fingerprint, real baseline runs, and pending human-review status.

## Stage 2.6 - RuadaptQwen3-4B Renderer Qualification

Stage 2.6 keeps the GPT policy and frozen `ResponseContract` artifacts from
Stage 2.5, but replaces only the wording renderer. This isolates the question
that the 0.6B experiment left open: can a stronger Russian model turn an
already-correct contract into a concise, coherent Telegram response?

The inference model is
`RefalMachine/RuadaptQwen3-4B-Instruct-GGUF` at pinned revision
`da30124570330edcb7fe487c5b1f1ba0b0c09721`. The future training base is
`RefalMachine/RuadaptQwen3-4B-Instruct` at pinned revision
`03bcd55e56b02175bcc863c4761613b1bda8302b`. Q6_K is primary; Q5_K_M is an
explicit fallback only when Q6_K cannot remain fully offloaded with safe VRAM.
No floating Hugging Face revision or silent quantization fallback is allowed.

Download, start, verify, and inspect the model:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File `
  scripts\local_slm\download_ruadapt_qwen3_4b.ps1 -Quantization Q6_K
powershell -NoProfile -ExecutionPolicy Bypass -File `
  scripts\local_slm\start_ruadapt_qwen3_4b_cuda.ps1
powershell -NoProfile -ExecutionPolicy Bypass -File `
  scripts\local_slm\check_ruadapt_qwen3_4b.ps1
uv run python -m conversation_agent local-model-doctor `
  --profile ruadapt-qwen3-4b
```

The CUDA launcher uses context 4096, one parallel slot, Flash Attention, KV
cache offload, and all supported GPU layers. The check refuses readiness
unless the manifest, served model, CUDA logs, full layer offload, VRAM delta,
`nvidia-smi` process, Russian completion, and non-thinking output all agree.
The exact file size and SHA-256 are recorded under `.runtime/local_slm/`.

Run the deterministic quick qualification with the saved Stage 2.5 contracts:

```powershell
uv run python -m conversation_agent benchmark stage26-run `
  --dataset benchmarks/local_slm_stage2_v1/scenarios.jsonl `
  --renderer ruadapt_qwen3_4b_q6 `
  --contracts-from .runtime/benchmarks/stage25-v1 `
  --baseline .runtime/benchmarks/stage25-v1 `
  --output .runtime/benchmarks/stage26-quick30 `
  --scenario-limit 30 `
  --gpu-required `
  --seed 42
```

The renderer receives only conversation context, relationship, known facts,
restrictions, goal, and the saved contract. It never receives expected
actions, evaluation notes, baseline outputs, or benchmark answers. The GPT
policy is not instantiated or called. Each contract is copied into a frozen
Stage 2.6 snapshot with its own fingerprint and source metadata.

The hard validator now records exact normalized copies, punctuation-only
copies, near copies, and substantial partial copies from any incoming message.
It stores the rule ID, similarity, token overlap, and matched fragment while
allowing concise reuse of known required facts. A failing local rendering gets
one retry from the same model; GPT does not repair it.

If quick-30 reaches at least 95% completion and schema validity, remains
stable on CUDA, and materially improves input repetition, run all 100 frozen
scenarios:

```powershell
uv run python -m conversation_agent benchmark stage26-run `
  --dataset benchmarks/local_slm_stage2_v1/scenarios.jsonl `
  --renderer ruadapt_qwen3_4b_q6 `
  --contracts-from .runtime/benchmarks/stage25-v1 `
  --baseline .runtime/benchmarks/stage25-v1 `
  --output .runtime/benchmarks/stage26-v1 `
  --gpu-required `
  --seed 42

uv run python -m conversation_agent benchmark stage26-report `
  --run .runtime/benchmarks/stage26-v1 `
  --baseline .runtime/benchmarks/stage25-v1 `
  --output .runtime/benchmarks/stage26-v1/report
```

The report imports both Stage 2.5 renderer baselines without rerunning them
and creates a compact diagnostic pack instead of a rating form. The
`READY_FOR_DATASET_PROTOTYPE` threshold means only that a small private
training-data prototype is technically justified. It does not mean production
readiness, autopilot approval, or guaranteed LoRA success.
