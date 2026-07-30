# Stage 3A Adaptive Style Results

## Identity

- Date: 2026-07-30
- Branch: `experiment/local-telegram-slm`
- Renderer source commit: `f9304b1bb4e760656c8c1978e245aed286198c26`
- Contract version: 2
- Frozen benchmark fingerprint:
  `55ed2c40dc8fc5723732a25863ea988f2ecfa7d00471720508eb56c5fc2405f4`
- Source Stage 2.6 snapshot:
  `1be968429b511f02c08289533b694d984d436305e0fb0e2664df7221f0362ddb`
- Adaptive contract snapshot:
  `394b906653c0e37fe102697fc973cb2dfe59350bb78cf7e5159e0669b09ea6ed`
- Full run fingerprint:
  `b7b5b7704f614cbce9a64c19f3655ebb0e08dd5978b4d8f48c7d4fc9c3489f48`

## Architecture

V1 artifacts remained readable and were migrated into `SemanticPlan` without
rewriting historical files. `AdaptiveStyleResolver` generated a fresh
`AdaptiveStylePlan` for every scenario from agent, relationship, current
conversation, and relationship-context signals. Every plan recorded evidence,
confidence, source weights, fallback state, and reasons.

Hard semantic, safety, and soft style validation ran independently. Soft style
deviations did not trigger provider failure or regeneration. Regeneration was
limited to hard semantic and safety failures.

The private dataset schema and all six local CLI operations validated
successfully on an empty prototype. Confirmed human manual/edit/fix/imported
text is eligible; accepted unchanged AI output and benchmark data are blocked.
No private example was imported.

## CUDA

- Model: `RefalMachine/RuadaptQwen3-4B-Instruct-GGUF`
- Revision: `da30124570330edcb7fe487c5b1f1ba0b0c09721`
- Quantization: `Q6_K`
- GPU: `NVIDIA GeForce RTX 5060 Laptop GPU`
- GPU offload: `37/37`
- VRAM: `6,459 / 8,151 MiB`
- CPU fallback: false
- Doctor: `Ready: YES`

## Quick 30

- Completion rate: 100%
- Hard semantic validity: 90%
- Safety validity: 100%
- Soft style fit: 77.46%
- Fallback rate: 66.67%
- Renderer retry rate: 16.67%
- Median / P90 latency: 1,298 / 2,006 ms
- Average speed: 39.489 tokens/s
- CUDA errors: 0

Quick inference was stable, so the full run proceeded. Soft style fit is a
diagnostic aggregate, not a human-likeness score.

## Full 100

- Completion rate: 100%
- Hard semantic validity: 90%
- Safety validity: 99%
- Soft style fit: 83.28%
- Casing fit: 99%
- Bubble distribution fit: 88%
- Length distribution fit: 28%
- Punctuation fit: 33%
- Greeting fit: 94.33%
- Emoji fit: 93%
- Question fit: 91%
- Formality fit: 89.09%
- Warmth / directness fit: 100% / 100%
- Lexical style fit: 98%
- Conversational rhythm fit: 86%
- Fallback rate: 80%
- Average style confidence: 16.76%
- Average evidence count: 1.23
- Agent / relationship profile confidence: 0 / 0
- Renderer retry rate: 13%
- Unsupported required-meaning flags: 1
- Forbidden-claim violations: 1
- Allowed-commitment violations: 0
- Sensitive-data violations: 0
- Handoff violations: 0
- Median / P90 latency: 1,274 / 1,695 ms
- Average speed: 39.863 tokens/s
- CUDA errors: 0

The high fallback rate is expected: no private human style profiles were
imported, so benchmark plans relied only on current conversational evidence
and relationship metadata.

## Regression Cases

`business-004` selected lowercase casing with confidence `0.21`, evidence
`conversation:0` and `conversation:1`, and produced:

```text
привет, да, могу помочь с заказами
что именно интересует?
```

This was a per-turn decision. Formal `business-003` independently selected
normal casing with confidence `0.21` and produced a capitalized formal answer.
`friendly-036` had insufficient evidence and explicitly used
`neutral_fallback`.

## Decision

Technical status: `READY_TO_COLLECT_HUMAN_EXAMPLES`.

The architecture is ready for a separately approved collection of 200-500
clean human examples. It is not ready for LoRA training yet. The diagnostic
pack is at `.runtime/benchmarks/stage3a-v1/diagnostic-pack/`; user qualitative
review of the examples remains pending.
