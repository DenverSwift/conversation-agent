# Architecture

## Current v0.2 boundaries

- `telegram/` filters events, handles Saved Messages feedback commands, and sends replies.
- `agent/` builds the current context and prompt.
- `llm/` owns the OpenAI client.
- `storage/` defines a replaceable feedback repository with a local SQLite implementation.
- `training/` contains deterministic extraction, grouping, cleaning, redaction, and export code.
- `tools/` contains thin command-line entrypoints.

Telegram handlers depend on the `FeedbackRepository` protocol rather than SQLite directly. This keeps a future PostgreSQL implementation from requiring handler rewrites.

When feedback is enabled, the generated reply record is created before Telegram delivery. If this initial local write fails, delivery is blocked so an untracked AI message cannot later be mistaken for a human-authored training target. Once Telegram returns a message ID, that ID is stored and used by the history exporter. Feedback-disabled mode bypasses storage and retains the v0.1 reply flow.

Telegram delivery and SQLite cannot share one atomic transaction. A failure after Telegram accepts a message but before its returned ID is persisted is logged without private text and requires manual dataset review.

## Future personalization layers

The intended order is:

1. Global Matvey style.
2. Per-contact communication profile.
3. Current conversation tone.
4. Relevant real examples.
5. Future fine-tuning based only on reviewed data.

These layers, contact classification, embeddings, fine-tuning, and automatic retraining are not implemented in v0.2.

No architecture decisions are final until documented in `docs/decisions/`.
