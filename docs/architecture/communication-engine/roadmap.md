# Roadmap

The roadmap advances capability only after trust invariants are proved. Letter
release names must follow [project versioning](../../versioning.md); the phases
below intentionally avoid assigning a release letter before product planning.

## Now: contracts and safe pilot foundation

- Freeze AA.2 behavior with provenance, compiler, and retrieval regression
  tests.
- Introduce workspace, membership, identity, contact, conversation, message,
  generated reply, approval, feedback, and audit contracts.
- Define `MessageEnvelope`, inbox/outbox, stable idempotency keys, and the
  Telegram legacy adapter.
- Keep a manually reviewed contact list; do not add lead sourcing.
- Persist versioned prompt plans and immutable human/AI/imported/system
  provenance.
- Implement policy checks, opt-out, draft-only, approval-required, and kill
  switches.
- Establish replay evaluation and cross-tenant test suites.

Exit: a Telegram-first pilot can draft, approve, deliver, receive, and audit
without duplicate sends or AI-to-human evidence contamination.

## Next: business workflow and workspace operation

- Deploy PostgreSQL with RLS and tenant-aware repositories, queues, caches, and
  object paths.
- Add sales/support playbooks, workflow transitions, campaign enrollment,
  frequency caps, timers, escalation, and operator queues.
- Add controlled CRM import/export and a minimal web control panel for
  workspace, campaign, policy, approval, and audit views.
- Add sourced fact, event, and relationship observations with correction and
  invalidation.
- Add PostgreSQL FTS retrieval and quality/cost dashboards.
- Run shadow mode, restricted approval-required pilots, security tests, and
  retention/export/deletion exercises.

Exit: multiple workspaces can operate bounded campaigns and support flows with
measured quality and policy compliance.

## Later: measured extensions

- Add email and approved messaging channel adapters plus CRM/help-desk
  integration; preserve one unified conversation model.
- Benchmark pgvector and rerankers; adopt only on a representative evaluation
  set.
- Evaluate Mem0/Supermemory adapters and Graphiti temporal retrieval without
  ceding authoritative provenance.
- Adopt Temporal or extract a module only after queue and deployment evidence.
- Consider narrowly scoped auto-send after sustained use-case-specific safety
  thresholds.

## Rejected until new evidence

Microservices by default, CQRS, full event sourcing, graph as the primary
database, autonomous lead sourcing, unreviewed bulk outreach, human
impersonation, and per-person fine-tuning.
