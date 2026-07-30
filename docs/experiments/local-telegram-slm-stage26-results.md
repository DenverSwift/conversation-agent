# Stage 2.6 RuadaptQwen3-4B Results

## Run Identity

- Date: 2026-07-30
- Branch: `experiment/local-telegram-slm`
- Source commit: `7c0d54c71851e7c25989f7017d0970e8c5b58ba0`
- Benchmark fingerprint:
  `55ed2c40dc8fc5723732a25863ea988f2ecfa7d00471720508eb56c5fc2405f4`
- Full run fingerprint:
  `0b11525ff1f6f6f7ffdfaf96f625367e627d351b2578a14ef3bc8bd7a0b87815`
- Contract snapshot fingerprint:
  `1be968429b511f02c08289533b694d984d436305e0fb0e2664df7221f0362ddb`

## Model And Runtime

- GGUF repository: `RefalMachine/RuadaptQwen3-4B-Instruct-GGUF`
- GGUF revision: `da30124570330edcb7fe487c5b1f1ba0b0c09721`
- Source repository: `RefalMachine/RuadaptQwen3-4B-Instruct`
- Source revision: `03bcd55e56b02175bcc863c4761613b1bda8302b`
- Architecture base: `Qwen/Qwen3-4B-Instruct-2507`
- Filename: `Q6_K.gguf`
- Quantization: `Q6_K`
- Size: 3,295,488,128 bytes
- SHA-256:
  `a206b1994822653e1da29ce76e96dc57f0f2a899f09a44466b94d3c043b82d29`
- llama.cpp: `b10194` (`e1a1abb78`)
- CUDA runtime package: `12.4`
- GPU: `NVIDIA GeForce RTX 5060 Laptop GPU`
- Offloaded layers: `37/37`
- VRAM: `7,081 / 8,151 MiB`; measured delta `3,076 MiB`
- Flash Attention: enabled
- KV cache offload: enabled
- CPU fallback: false
- Doctor: `Ready: YES`

The server returned a real Russian completion and a valid structured response.
No `<think>` or `reasoning_content` was emitted. The loaded alias,
quantization, revision, hash, full layer offload, process visibility, and VRAM
increase were checked against the runtime manifests.

## Quick 30

- Completion rate: 100%
- Schema validity: 100%
- Renderer validity: 86.67%
- Retry rate: 20%
- Exact-copy rate: 3.33%
- Near/partial-copy rate: 3.33%
- Repeated-question rate: 3.33%
- Forbidden-claim rate: 3.33%
- Required-fact coverage: 96.7%
- Median renderer latency: 1,060 ms
- P90 renderer latency: 1,603 ms
- Average generation speed: 42.713 tokens/s
- CUDA errors: 0

Quick qualification passed the conditions for a full run: all requests and
schemas completed, CUDA remained stable, no thinking output appeared, and
incoming-message repetition fell substantially from the Qwen3-0.6B baseline.

## Full 100

- Completion rate: 100%
- Schema validity: 100%
- Renderer validity: 87%
- Retry rate: 22%
- Exact-copy rate: 1%
- Near/partial-copy rate: 3%
- Repeated-question rate: 1%
- Forbidden-claim rate: 1%
- Required-fact coverage: 97%
- Bubble-limit compliance: 100%
- Exact target-bubble compliance: 99%
- Total-character compliance: 100%
- Per-bubble character compliance: 100%
- Question-count compliance: 94%
- Unsupported-fact flags: 5
- Empty replies: 0%
- Truncated outputs: 0%
- Incomplete-sentence flags: 0%
- Median renderer latency: 1,009 ms
- P90 renderer latency: 1,690 ms
- Average generation speed: 45.687 tokens/s
- CUDA errors: 0

For comparison, the saved Qwen3-0.6B renderer had 59% renderer validity, 41%
retry rate, and about 30% repeated-question rate. The saved OpenAI renderer
had 89% renderer validity and 14% retry rate. No baseline inference or GPT
policy call was repeated for Stage 2.6.

## Decision

Technical status: `READY_FOR_DATASET_PROTOTYPE`.

RuadaptQwen3-4B passed the automatic threshold for trying a small private
training dataset prototype. This is not a production, autopilot, or Telegram
deployment approval, and it does not guarantee that LoRA will solve the
remaining style and contract-compliance errors.

The compact diagnostic pack is at
`.runtime/benchmarks/stage26-v1/diagnostic-pack/`. User qualitative review is
pending; no human quality score has been invented.
