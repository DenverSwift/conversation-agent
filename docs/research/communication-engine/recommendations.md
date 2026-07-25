# Recommendations

## Now

- Preserve the AA.2 runtime and document it as a legacy adapter during
  transition.
- Define channel-neutral `MessageEnvelope`, immutable provenance, and
  generation/approval/delivery records.
- Model workspace, membership, identity, contact, conversation, message,
  communication policy, and feedback in a modular monolith.
- Add draft and approval-required modes before any broader outbound capability.
- Keep style compilation provider-independent and evidence allow-listed.

## Next

- Move durable business data to PostgreSQL with row-level security.
- Add inbox/outbox workers, idempotency keys, retries, rate limits, quiet hours,
  do-not-contact, frequency caps, and a global auto-send kill switch.
- Add contact/lead, campaign, playbook, lead-stage, follow-up, escalation, and
  audit records.
- Implement PostgreSQL FTS ranking across examples/facts; benchmark pgvector.
- Introduce offline factual/relationship extraction with source links and
  corrections.
- Record a versioned prompt plan: component versions, selected evidence IDs,
  scores, exclusions, budgets, model, and policy decision.

## Later

- Additional channel adapters and CRM integration.
- Optional memory-provider adapters (Mem0/Supermemory) evaluated against the
  internal contract.
- Graphiti only for demonstrated multi-hop/temporal retrieval failures.
- Temporal only when database jobs cannot reliably support campaign volume.
- Open-weight fine-tuning only as a separately consented experiment.

## Reject for now

- microservices, CQRS, or full event sourcing;
- graph database as primary store;
- a separate deployable Identity/Style/Goal/Relationship service;
- mandatory LangGraph/agent framework;
- per-user fine-tuned models;
- autonomous lead sourcing or unapproved bulk auto-send;
- using caller-provided IDs as the only tenant boundary.
