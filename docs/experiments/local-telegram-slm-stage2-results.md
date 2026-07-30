# Local Telegram SLM Stage 2 Results

## Run Identity

- Date: 2026-07-30
- Branch: `experiment/local-telegram-slm`
- Source commit: `2ffb1f36368b317fa25738c8e672b4c9f9dbbc55`
- Benchmark: `local-slm-stage2-v1`
- Benchmark fingerprint:
  `55ed2c40dc8fc5723732a25863ea988f2ecfa7d00471720508eb56c5fc2405f4`
- Scenarios: 100
- Categories represented: 36
- Review seed: 42
- Local provider: `Qwen/Qwen3-0.6B-GGUF:Q8_0` through real llama.cpp
- OpenAI provider: real `gpt-4o-mini` Responses API calls with `store=false`
- Fake providers used in real runs: no

The benchmark is public, manually authored, and benchmark-only. It was not
created from private Telegram conversations and was not used for training.

## Real Run Status

| Mode | Provider | Successful outputs | Failures | Schema validity | Median latency | P90 latency |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| system comparison | local Qwen | 99/100 | 1 | 96.97% | 5405 ms | 8695 ms |
| system comparison | GPT-4o-mini | 100/100 | 0 | 100.00% | 1127 ms | 1584 ms |
| same context | local Qwen | 100/100 | 0 | 100.00% | 5072 ms | 6264 ms |
| same context | GPT-4o-mini | 100/100 | 0 | 100.00% | 1281 ms | 1921 ms |

Both full modes completed. Across the two modes there were 199 successful real
local outputs and 200 successful real OpenAI outputs. Runtime artifacts remain
under `.runtime/benchmarks/` and are not committed.

## Automatic Observations

In system comparison, expected-action match was 72.73% for local Qwen and
99.00% for GPT-4o-mini. In same-context comparison it was 62.00% and 98.00%.
This suggests that action selection, especially `no_reply`, `reaction`, and
`handoff`, needs close human inspection before training-data work begins.

Local Qwen produced valid normalized output in 96.97% of system scenarios and
100% of same-context scenarios. Its median latency was about 5 seconds on the
CPU-only machine, with average throughput between 8.47 and 8.74 tokens/second.
The configurable phrase detector flagged frequent unnecessary question
repetition in local outputs. Detector flags are diagnostic and are not human
quality judgments.

Required-fact coverage was 93.9%/93.0% for local Qwen and 96.0%/96.0% for
GPT-4o-mini in system/same-context runs. Automatic unsupported-fact flags were
1/0 for local and 1/2 for GPT. These flags require human verification and do
not by themselves establish hallucinations.

No winner is declared from automatic metrics.

## Technical Errors

The system-comparison local run recorded one provider failure for
`flow-043`. Qwen returned an invalid `confidence` value on both the initial
generation and its one local repair retry. The runner saved the failed result
and continued. The same scenario completed successfully in same-context mode.

No OpenAI provider failures or timeouts occurred. OpenAI token usage was saved,
but monetary cost was not calculated because no explicit per-million-token
prices were configured.

## Human Review

Human blind review pending.

- Human-reviewed scenarios: 0
- Available system-comparison A/B pairs: 99
- Available same-context A/B pairs: 100
- Stage 3 decision: pending

At least 30 genuine human ratings are required before deciding whether the
experiment is ready for the training-data stage. No human ratings were
generated or inferred during implementation.

## Continue And Reproduce

Resume system comparison without repeating successful calls:

```powershell
python -m conversation_agent benchmark stage2-run `
  --dataset benchmarks/local_slm_stage2_v1/scenarios.jsonl `
  --mode system_comparison `
  --providers local_qwen,openai_gpt4o_mini `
  --output .runtime/benchmarks/stage2-system-v1 `
  --seed 42 `
  --resume
```

Retry the saved local provider error:

```powershell
python -m conversation_agent benchmark stage2-run `
  --dataset benchmarks/local_slm_stage2_v1/scenarios.jsonl `
  --mode system_comparison `
  --providers local_qwen,openai_gpt4o_mini `
  --output .runtime/benchmarks/stage2-system-v1 `
  --seed 42 `
  --resume `
  --retry-errors
```

Start or resume blind review:

```powershell
python -m conversation_agent benchmark stage2-review `
  --run .runtime/benchmarks/stage2-system-v1 `
  --reviewer denver `
  --seed 42 `
  --only-unreviewed
```

Generate the current report:

```powershell
python -m conversation_agent benchmark stage2-report `
  --run .runtime/benchmarks/stage2-system-v1 `
  --reviews .runtime/benchmarks/stage2-system-v1/reviews `
  --output .runtime/benchmarks/stage2-system-v1/report
```

The remote GPT API is not claimed to be fully deterministic. Dataset,
configuration, run fingerprints, and A/B ordering are deterministic.
