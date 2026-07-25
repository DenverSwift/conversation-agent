# Current System Audit

## Baseline

Audited commit:
[`d91bc21ed4748e0773be3d382d0bc79543c23451`](https://github.com/DenverSwift/conversation-agent/tree/d91bc21ed4748e0773be3d382d0bc79543c23451),
release `AA.2`.

## Runtime and boundaries

The application is a local Python modular monolith with two processes:

- the Telethon agent accepts text from one configured private contact, builds
  recent context, calls the OpenAI Responses API, and sends a reply
  ([Telegram handler](../../../src/conversation_agent/telegram/handlers.py));
- the Bot API Trainer Bot presents review cards and writes feedback
  ([trainer service](../../../src/conversation_agent/trainer/service.py)).

The agent, trainer, compiler, exporters, and inspection tools share local files
and SQLite. `FeedbackRepository` is already a useful port around persistence
([protocol](../../../src/conversation_agent/storage/repository.py)).

## Storage and delivery

`generated_replies` stores incoming/reply text, model, prompt version,
conversation snapshot, Telegram delivery ID/status, feedback, correction, and
notification state
([schema](../../../src/conversation_agent/storage/sqlite_repository.py)).
WAL, short transactions, stale notification claims, and stored trainer message
IDs address local concurrency and duplicate review cards.

The reply record is created before send; a storage failure blocks delivery.
Telegram acceptance and SQLite update cannot be atomic. The remaining failure
window is explicitly documented and must become a transactional outbox/inbox
problem in a multi-channel system.

## Message provenance

Recent outgoing Telegram IDs are cross-checked against generated-reply records.
Context labels are `contact`, `human_matvey`, or `ai_generated`
([context builder](../../../src/conversation_agent/agent/context_builder.py)).
Exports include only human-authored targets and exclude known generated IDs
([exporter](../../../src/conversation_agent/training/exporter.py)).

This is a core product invariant worth generalizing to
`contact | human_operator | ai_generated | imported | system`, with immutable
`origin`, `author_actor_id`, `generation_id`, and external-message identity.

## Style compiler and request composition

The compiler canonicalizes source keys, hashes style-relevant content, reuses
unchanged/content-identical observations, analyzes only deltas, removes deleted
contributions, and atomically publishes bundle plus SQLite state
([compiler](../../../src/conversation_agent/style/compiler.py);
[state schema](../../../src/conversation_agent/style/compiler_state.py)).
Fingerprint changes require `--full-rebuild`; identical builds make zero model
requests. Tests cover modification, deletion, duplicate content, failure
rollback, private summaries, and no-op behavior
([tests](../../../tests/test_incremental_style_compiler.py)).

Runtime retrieval is local TF-IDF plus intent/recency/contact heuristics; Fix
receives highest priority
([retriever](../../../src/conversation_agent/style/retrieval.py)).
Composition orders core instructions, manual overrides, compiled rules, contact
profile, examples, and recent history with character budgets
([composer](../../../src/conversation_agent/style/composer.py)).

## Feedback and exports

Trainer actions are Good, Bad(category/comment), Fix, and Should not reply.
Fix is human evidence; approved AI text remains evaluation data and never
positive style evidence. Exporters are provider-independent and private
([feedback exporter](../../../src/conversation_agent/training/feedback_export.py)).

## Local-only data

`.env`, Telegram sessions, feedback DB, exports, compiler DB, generated bundle,
manual overrides, evaluation output, and logs are ignored. Git is code/docs
source of truth only. A device without private evidence can still run tests from
fixtures. This boundary must be preserved while adding an explicit secure
deployment/data-sync design later.

## What should survive

- immutable message provenance and generated-reply linkage;
- write-before-send tracking and idempotent notification claims;
- repository ports rather than handler-to-SQL coupling;
- provider-independent exports and prompt construction;
- human Fix priority and AI-evidence exclusion;
- incremental compilation, analysis fingerprints, and atomic publication;
- safe metadata-only inspection and private-content opt-in.

## Current limits

- one configured user, contact, Telegram dialog, identity, and process;
- Telegram types leak into orchestration and history acquisition;
- SQLite schema is feedback-centric, not a communication domain;
- no workspace/RBAC/RLS, contacts, leads, campaigns, playbooks, opt-out, or
  approval queue;
- no channel-neutral inbox/outbox or durable job scheduler;
- retrieval is style-only and does not rank facts, policies, or workflow state;
- no stable prompt-plan record containing selected evidence and reasons;
- no business outcome evaluation, rate/frequency policies, or kill switch.
