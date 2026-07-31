# Stage 3E Authorship Verification Results

## Identity

- Date: 2026-07-31
- Branch: `experiment/local-telegram-slm`
- Starting commit: `05aa0b27ffcf70de11878a0c14f6221b7a44e208`
- Status: `READY_FOR_AUTHORSHIP_REVIEW`
- User review: pending

## Reconciliation

- Source pilot examples: 82
- Transport-authoritative examples: 82
- Human-authored examples with separate evidence: 0
- Human-edited-AI examples with separate evidence: 0
- Exact AI-authored examples in the selected pilot: 0
- Conflicting-authorship examples: 0
- Unknown-authorship examples: 82
- Local audit source aliases checked: 2

The previous transport verdict was not migrated into human authorship.
Telegram-history evidence remained transport-only. No heuristic flag changed
an authorship verdict.

## Review

- Suspicious examples detected: 28
- Bounded review entries generated: 20
- Required regression positions covered: 11 of 11
- Unresolved authorship decisions: 82
- Approved authorship decisions: 0

The review queue includes the explicitly required positions and additional
high-priority style or context anomalies. Suspicion is not reported as
confirmed AI without separate audit evidence.

## Conservative Preview

- Automatically retained examples: 0
- Conservatively excluded examples: 82
- Minimum required examples: 50
- PII inside the proposed clean pilot: 0
- Clean preview status: `INSUFFICIENT_AUTHORSHIP_VERIFIED_DATA`
- Synthetic examples created: 0
- Text normalization performed: false

With no user approvals and no separate human-input audit for the selected
records, conservative mode excluded all unresolved authorship. This is an
intentional safety result rather than evidence that all excluded records were
AI-authored.

## Safety

- Final confirmation executed: false
- Private profile built: false
- OpenAI called: false
- Local LLM called: false
- Embeddings called: false
- Training started: false
- Production generation default changed: false

The user must review the bounded authorship queue and provide explicit
decisions. A clean pilot cannot be confirmed until at least 50 retained
records have acceptable authorship provenance or explicit human approval.
