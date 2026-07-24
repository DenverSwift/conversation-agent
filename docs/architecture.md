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

## Current generation path

The implemented AAA.3 runtime path is:

```text
incoming message
-> global Matvey behavior loaded from README
-> up to 30 recent Telegram messages
-> configured OpenAI base model
-> Telegram reply
```

The runtime does not read `raw_examples.jsonl`, `cleaned_examples.jsonl`, or
feedback export files. It does not retrieve Fix corrections. Therefore, the
configured export limit of 500 has no effect on generation today.

## AA.1 runtime style adaptation

Dynamic few-shot retrieval is an AA.1 implementation requirement:

```text
incoming message
-> global Matvey style profile
-> contact-specific real human examples
-> high-priority relevant Fix corrections
-> recent conversation with provenance
-> configured OpenAI base model
-> Telegram reply
```

AA.1 must select a small set of relevant examples rather than sending the
complete 500-message dataset on every request. Selection and prompt assembly
must preserve provenance so the application can enforce these evidence rules:

- only real human-authored Matvey messages are style evidence;
- AI-generated replies are never style evidence, even when approved;
- rejected replies are never positive examples;
- corrected Fix replies are human-authored evidence and have the highest
  retrieval priority;
- recent conversation remains conversation context, not automatically style
  evidence;
- the configured base model is used without modifying its weights.

Feedback collection, dataset export, runtime retrieval, and model training are
separate concerns. The exporters remain provider-independent and may support
retrieval, evaluation, prompt development, or optional future training of an
open-weight model. They do not upload files or create hosted models.

OpenAI is winding down self-service fine-tuning, so an OpenAI-hosted custom
model is not part of the roadmap. See
[`adr/0001-runtime-style-adaptation.md`](adr/0001-runtime-style-adaptation.md).

No architecture decisions are final until documented in `docs/adr/`.
