# Deployment Evolution

## Evolution, not rewrite

The target begins as a modular monolith with separately runnable web/API and
worker processes built from one codebase. Module boundaries are enforced in
code and ownership tables, not by network calls. The current AA.2 Telegram and
trainer processes remain usable while adapters are introduced.

| Stage | Runtime | Data | Primary proof |
|---|---|---|---|
| AA.2 baseline | Existing Telethon agent and trainer | Local SQLite and files | Preserve current personal assistant behavior |
| Sales pilot | Modular core plus Telegram legacy adapter | SQLite only for local development; PostgreSQL for pilot | Provenance, approval, policy, inbox/outbox |
| Workspace product | API and worker replicas | PostgreSQL with RLS, object storage, secret manager | Tenant isolation and auditable operations |
| Multichannel | Connector workers by capability | Same domain store, channel-specific cursors | No channel leakage into core |
| Scale | Independently scaled workers; optional workflow/memory services | Read replicas, partitions, optional pgvector | Measured bottleneck relief |

## Reliability model

Domain changes and outbox events commit in one database transaction. Delivery
and extraction use at-least-once jobs with idempotent handlers. Each job has
`pending`, `running`, `succeeded`, `retry_scheduled`, `failed`, or
`dead_letter` status, attempt records, next-attempt time, and a stable
idempotency key.

Retry only classified transient failures with exponential backoff and jitter.
Authentication, policy denial, invalid payload, and permanent platform errors
go directly to operator-visible failure. Unknown delivery outcomes are
reconciled before retry. Dead letters support inspect, correct, replay, or
cancel with audit.

## Failure scenarios

| Failure | Required behavior |
|---|---|
| Duplicate inbound webhook/update | Unique inbox key returns prior result |
| Crash after message persistence | Unprocessed inbox event is reclaimed |
| Crash after provider accepted send | Reconcile external ID; never create a new logical send |
| Provider outage/rate limit | Respect retry-after, cap retries, preserve ordering |
| Model timeout | No delivery; retry draft or hand to operator |
| Policy changed after approval | Pre-send check blocks or requests approval again |
| Opt-out races with queued send | Opt-out transaction cancels unsent delivery |
| Extraction/compiler failure | Conversation remains valid; derived version is not published |
| Tenant context missing | Fail closed before any data access |

## Scaling

Partition queues by workspace and connector account while limiting noisy
tenants. Apply per-workspace, per-channel, and global concurrency controls.
Index by `(workspace_id, status, due_at)` and archive completed attempts.
Separate model, extraction, compilation, and delivery worker pools so expensive
inference cannot starve opt-out or delivery reconciliation.

Monitor queue age, policy denials, approval latency, retry rate, unknown sends,
duplicate suppression, opt-outs, cross-tenant test failures, retrieval latency,
model cost, and prompt-token budgets. Distributed traces use correlation IDs
and redacted attributes.

Only extract a module into a service after measured scaling, isolation,
deployment, or regulatory pressure. Keep PostgreSQL as the consistency
boundary until that evidence exists.
