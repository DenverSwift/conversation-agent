# Local SLM Stage 2 Frozen Baseline

This directory is the public, Russian-only, test-only baseline for comparing
untrained Qwen3-0.6B with GPT-4o-mini before any LoRA work.

- Scenarios: 100 manually authored fixtures.
- Exact target replies: intentionally absent.
- Fingerprint: `55ed2c40dc8fc5723732a25863ea988f2ecfa7d00471720508eb56c5fc2405f4`
- Private Telegram data: not used.
- Training use: forbidden.

Good live replies can vary. Automatic checks use allowed actions, required
facts, forbidden claims, bubble limits and length limits. Final quality
judgment belongs to blind human review.

Version 1 is frozen after its first real run. Any scenario edit requires a new
`local_slm_stage2_v2` dataset and a new fingerprint.
