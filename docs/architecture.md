# Architecture

## Current AA.2 approval-first boundaries

- `telegram/` filters Telethon events, buffers each contact independently,
  invalidates stale work, and executes approved behavior plans.
- `trainer/` owns the separate private Bot API review UI, notifications, and
  idempotent trainer actions.
- `agent/` separates interaction analysis, goal planning, retrieval, prompt
  composition, response generation, and behavior planning.
- `llm/` defines a replaceable structured-output provider and the OpenAI
  Responses API implementation.
- `domain/` contains validated profiles, conversation state, drafts, plans, and
  provenance-aware message models.
- `storage/` defines repository boundaries and a backward-compatible local
  SQLite implementation.
- `training/` contains deterministic extraction, grouping, cleaning, redaction, and export code.
- `tools/` contains thin command-line entrypoints.

The production path is:

```text
incoming private message
-> per-contact accumulation
-> analysis and goal
-> relevant human evidence
-> structured response and behavior plan
-> pending SQLite draft
-> private Trainer Bot card
-> idempotent trainer action
-> approved Telegram behavior
```

No AI draft is delivered before approval. A newer incoming message marks
pending drafts for that contact stale. The runtime checks staleness, new
incoming messages, and a manual Matvey reply before every bubble. Reject and
Skip never send; Handoff disables automatic processing for that contact.

The agent process and trainer-bot process share SQLite through short
transactions. WAL mode and a busy timeout allow concurrent readers and writers.
Notification rows are claimed atomically before Bot API delivery; the stored
trainer chat/message IDs prevent duplicate cards. Interrupted claims become
retryable after a bounded stale interval.

The trainer bot accepts only the configured user in the configured private
chat. Callback payloads contain only an action and local draft ID. Cards contain
the incoming message group, proposed bubbles, decision metadata, and a safe
prompt summary, but never credentials, Telegram session data, or the complete
prompt.

SQLite storage and the private Trainer Bot are mandatory for this approval-first
runtime. Draft creation and its behavior plan are committed before notification.
If persistence fails, nothing is sent. Telegram delivery and SQLite cannot share
one atomic transaction, so a failure after Telegram accepts a bubble but before
its ID is persisted is logged without private text and requires manual review.

Schema version 3 preserves historical `generated_replies` and trainer records
while adding profiles, conversations, state, provenance-aware messages,
behavior plans, drafts, normalized feedback, retrieved-example metadata,
runtime events, handoffs, and idempotent trainer actions.

## AA.1 runtime style adaptation

Dynamic few-shot retrieval is the implemented AA.1 path:

```text
incoming message
-> global Matvey style profile
-> contact-specific real human examples
-> high-priority relevant Fix corrections
-> recent conversation with provenance
-> configured OpenAI base model
-> Telegram reply
```

AA.1 selects a small set of relevant examples rather than sending the
complete 500-message dataset on every request. Selection and prompt assembly
must preserve provenance so the application can enforce these evidence rules:

- only real messages authored by the configured identity owner are style evidence;
- AI-generated replies are never style evidence, even when approved;
- rejected replies are never positive examples;
- corrected Fix replies are owner-authored evidence and have the highest
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

## AA.2 incremental compiler

The compiler canonicalizes every qualifying source into a stable source key and
a SHA-256 hash of normalized style-relevant fields. Private SQLite state maps
each source to structured observations and retains a content-hash cache.
Unchanged evidence and duplicate content reuse that cache. New or modified
unique hashes are analyzed in bounded delta batches.

Final rules are merged locally from all active per-source observations.
Supporting source keys and hashes make deletions reversible: removing a source
removes its contribution without resending unchanged raw messages. Generated
bundle files and a replacement compiler database are staged first and
published only after analysis and validation succeed.

An analysis fingerprint covers the model, prompt template, observation schema,
compiler and normalization versions, batch configuration, and evidence policy.
Incompatible state stops the build until a contributor explicitly requests
`--full-rebuild`.
