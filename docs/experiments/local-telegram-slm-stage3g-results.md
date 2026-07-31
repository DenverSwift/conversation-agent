# Stage 3G Relationship-Conditioned Renderer Shadow A/B

## Identity

- Date: 2026-07-31
- Branch: `experiment/local-telegram-slm`
- Starting commit: `8f3e85e814cda3fe77771f99a5ec234d052ad1db`
- Profile schema: 2
- Model: `RefalMachine/RuadaptQwen3-4B-Instruct-GGUF`, Q6_K
- Status: `READY_FOR_BLIND_REVIEW`

All generation and review artifacts remain private and Git ignored. This
document contains aggregate results only.

## Tracks

| Track | Pairs | Candidates | Completed |
| --- | ---: | ---: | ---: |
| Frozen controlled contracts | 20 | 40 | 40 |
| Exploratory private shadow | 10 | 20 | 20 |
| Total | 30 | 60 | 60 |

The controlled track isolates renderer presentation over frozen Stage 2.6
semantics. The private track is exploratory end-to-end generation with a
leave-one-out relationship profile for every held-out episode.

## Automatic Results

- Completion rate: 1.0
- Schema validity: 1.0
- Hard semantic validity: 0.916667
- Safety validity: 0.966667
- Private phrase leakage: 0
- Incoming-copy rate: 0.0
- Profanity use: 0
- Profanity misuse: 0
- Assistant-like phrase rate: 0.0
- Retry rate: 0.133333
- Style-plan adherence: 0.874617
- Median latency: 1030.5 ms
- P90 latency: 2118.2 ms
- Median generation speed: 42.725 tokens/s

Neutral and relationship-conditioned candidates had equal counts and complete
generation. Their semantic and safety fingerprints matched within every pair.
Both variants had one forbidden-claim failure and one safety failure. Neither
variant copied incoming text, leaked private phrases, made unsupported
commitments, exposed sensitive data, or misused profanity. The automatic
safety comparison found no relationship-conditioned regression.

## Model Verification

- Backend: llama.cpp CUDA
- GPU: NVIDIA GeForce RTX 5060 Laptop GPU
- Offloaded layers: 37
- CPU fallback: false
- Non-thinking structured output: verified
- OpenAI fallback: disabled
- Pinned manifest and revision: verified

## Blind Review

The review queue contains 30 pairs and no human ratings. Candidate labels are
deterministically blinded, while execution order is independently randomized.
The private human target remains hidden until a rating is saved.

```powershell
uv run python -m conversation_agent benchmark stage3g-review-ui `
  --run .runtime/benchmarks/stage3g-v1 `
  --reviewer denver `
  --seed 42
```

The next action is human blind review. No LoRA, QLoRA, training-readiness, or
global-style conclusion follows from this single-relationship experiment.

## Safety

- Telegram changed: false
- Messages or reactions sent: false
- Private targets passed to the model: false
- Exact profile phrases passed to the model: false
- OpenAI called: false
- Embeddings called: false
- Training started: false
- Production generation default changed: false
