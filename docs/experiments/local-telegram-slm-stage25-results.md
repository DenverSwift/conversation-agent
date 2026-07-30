# Local Telegram SLM Stage 2.5 Results

## Run Identity

- Date: 2026-07-30
- Branch: `experiment/local-telegram-slm`
- Benchmark source commit: `d3edd0b174967ea5a03ce056b76943d328bc254f`
- Benchmark: `local-slm-stage2-v1`
- Benchmark fingerprint:
  `55ed2c40dc8fc5723732a25863ea988f2ecfa7d00471720508eb56c5fc2405f4`
- Policy: real `gpt-4o-mini` Responses API with structured output
- OpenAI renderer: real `gpt-4o-mini` Responses API
- Local renderer: real `Qwen/Qwen3-0.6B-GGUF:Q8_0`
- Fake providers used in real runs: no
- Training performed: no

The frozen Stage 2 scenarios were not modified or used for training. Existing
Stage 2 direct outputs were imported as references and were not regenerated.

## CUDA Verification

- GPU: NVIDIA GeForce RTX 5060 Laptop GPU
- Driver: 582.05; reported CUDA compatibility: 13.0
- llama.cpp: b10194, commit `e1a1abb78`
- Official runtime archive: Windows CUDA 12.4 x64
- CUDA device discovery: confirmed by `llama-server --list-devices`
- Offload: 29/29 model layers
- VRAM during verification: 4178/8151 MiB
- VRAM increase after model load: 1190 MiB
- `llama-server` process visible in `nvidia-smi`: yes
- CPU fallback: false
- Managed endpoint: `http://127.0.0.1:8080/v1`
- `local-model-doctor`: `Ready: YES`

The CUDA check used backend logs, offloaded-layer count, process visibility,
VRAM growth, health and model endpoints, and a real completion. GPU inference
was not inferred from the presence of `nvidia-smi` alone.

## Run Status

The 20-scenario quick run completed for all four comparison pipelines. It
covered the required action and conversation groups. Existing direct outputs
were imported without new provider calls.

The clean full run completed 100/100 scenarios for both new pipelines:

| Pipeline | Completed | Provider errors | Contract validity | Renderer validity |
| --- | ---: | ---: | ---: | ---: |
| GPT policy + OpenAI renderer | 100/100 | 0 | 100% | 89% |
| GPT policy + local Qwen renderer | 100/100 | 0 | 100% | 59% |

An initial full pass exposed five `reaction_missing` policy contracts. One
policy-only semantic repair was added and tested. The clean run then produced
100 valid contracts. Renderer retries still reuse the original policy plan.

## Action Metrics

| Pipeline | Action match | no_reply recall | handoff recall | reaction match |
| --- | ---: | ---: | ---: | ---: |
| Stage 2 local direct | 72.73% | 26.67% | 33.33% | 40.00% |
| Stage 2 OpenAI direct | 99.00% | 93.33% | 100.00% | 100.00% |
| GPT policy + OpenAI renderer | 100.00% | 100.00% | 100.00% | 100.00% |
| GPT policy + local Qwen renderer | 100.00% | 100.00% | 100.00% | 100.00% |

The shared ResponseContract policy fixed the action-selection weakness on this
benchmark. Expected actions were used only by the evaluation layer.

## Length And Validation

| Metric | OpenAI renderer | Local Qwen renderer |
| --- | ---: | ---: |
| Benchmark bubble compliance | 85% | 85% |
| Total character compliance | 100% | 100% |
| Per-bubble character compliance | 100% | 100% |
| Question-count compliance | 97% | 96% |
| Required-fact coverage | 96% | 96% |
| Forbidden-claim rate | 2% | 3% |
| Repeated incoming question rate | 0% | 30% |
| Automatic human-edit proxy | 3% | 32% |
| Renderer retry rate | 14% | 41% |

The OpenAI renderer's average output fell from 64.14 characters and 1.75
bubbles in the direct baseline to 49.19 characters and 0.81 bubbles. The local
renderer remained much less reliable: 33 repeated-question validation flags,
four question-limit failures, four missing required-fact failures, three
forbidden-claim failures, and four target-bubble mismatches.

Automatic flags are diagnostics, not a final human quality score.

## Latency And Usage

| Metric | OpenAI renderer pipeline | Local Qwen renderer pipeline |
| --- | ---: | ---: |
| Median policy latency | 2059 ms | 2059 ms |
| P90 policy latency | 3594 ms | 3594 ms |
| Median renderer latency | 1246 ms | 454 ms |
| P90 renderer latency | 2721 ms | 708 ms |
| Median total latency | 3357 ms | 2539 ms |
| P90 total latency | 5141 ms | 3776 ms |
| Local generation speed | n/a | 140.928 tokens/sec |

The Stage 2 CPU local direct baseline measured 5405 ms median, 8695 ms P90,
and 8.737 tokens/sec. CUDA substantially improved local rendering speed, while
the full pipeline still includes remote GPT policy latency.

OpenAI policy usage was saved separately from renderer usage:

- Policy: 56,612 prompt, 12,029 completion, 68,641 total tokens.
- OpenAI renderer: 32,560 prompt, 3,275 completion, 35,835 total tokens.
- Local renderer: 30,664 prompt, 3,235 completion, 33,899 total tokens as
  reported by the local endpoint.

## Diagnostics And Decision

- Stage 2 diagnostic pack:
  `.runtime/benchmarks/stage2-system-v1/diagnostic-pack`
- Stage 2.5 diagnostic pack:
  `.runtime/benchmarks/stage25-v1/report/diagnostic-pack`
- Stage 2.5 report:
  `.runtime/benchmarks/stage25-v1/report/report.md`
- Representative Stage 2.5 scenarios: 40, with no duplicate scenario IDs
- Stage 3 gate: `READY_FOR_ARCHITECTURE_EXPERIMENT`
- Technical recommendation: `TEST_LARGER_LOCAL_MODEL`
- Production ready: no
- User qualitative review: pending

The decomposition works for action selection and response sizing. Qwen3-0.6B
is fast on this GPU but remains unreliable as a contract-following renderer,
especially because it repeats the incoming question. A larger local model is
the next technical experiment; this stage does not select a model for
training, enable autopilot, or change the production default from
`openai_only`.
