# Architecture

## Current AAA.3 boundaries

- `telegram/` filters Telethon events and sends replies from Matvey's personal account.
- `trainer/` owns the separate private Bot API review UI and notification delivery.
- `agent/` builds the current context and prompt.
- `llm/` owns the OpenAI client.
- `storage/` defines a replaceable feedback repository with a local SQLite implementation.
- `training/` contains deterministic extraction, grouping, cleaning, redaction, and export code.
- `tools/` contains thin command-line entrypoints.

Telegram handlers depend on the `FeedbackRepository` protocol rather than SQLite directly. This keeps a future PostgreSQL implementation from requiring handler rewrites.

The agent process and trainer-bot process share SQLite through short
transactions. WAL mode and a busy timeout allow concurrent readers and writers.
Notification rows are claimed atomically before Bot API delivery; the stored
trainer chat/message IDs prevent duplicate cards. Interrupted claims become
retryable after a bounded stale interval.

The trainer bot accepts only the configured user in the configured private
chat. Callback payloads contain only an action and local reply ID. Cards contain
the incoming message and exact generated reply, but never full context,
prompts, credentials, or Telegram session data.

When feedback is enabled, the generated reply record is created before Telegram delivery. If this initial local write fails, delivery is blocked so an untracked AI message cannot later be mistaken for a human-authored training target. Once Telegram returns a message ID, that ID is stored and used by the history exporter. Feedback-disabled mode bypasses storage and retains the `AAA.1` reply flow.

Telegram delivery and SQLite cannot share one atomic transaction. A failure after Telegram accepts a message but before its returned ID is persisted is logged without private text and requires manual dataset review.

## Future personalization layers

The intended order is:

1. Global Matvey style.
2. Per-contact communication profile.
3. Current conversation tone.
4. Relevant real examples.
5. Future fine-tuning based only on reviewed data.

These layers, contact classification, embeddings, fine-tuning, and automatic retraining are not implemented in `AAA.3`.

No architecture decisions are final until documented in `docs/decisions/`.
