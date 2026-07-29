# Communication Engine Architecture

This is the proposed architecture after the
[landscape research](../../research/communication-engine/README.md). It is an
evolution target, not an instruction to implement production features in the
current release.

## Decision summary

- Start as a modular monolith, not microservices.
- Keep PostgreSQL as system of record; SQLite remains valid for the local AA.2
  stage.
- Keep identity, style, memory, workflow, and messages separately owned.
- Put immutable provenance on every message and derived evidence.
- Make channel receive/send idempotent through inbox/outbox records.
- Execute policy before generation and again before delivery.
- Default new outbound use cases to draft/approval-required.
- Use PostgreSQL FTS first; add pgvector only after evaluation; graph later.

## Documents

- [Product scope](product-scope.md)
- [Domain model](domain-model.md)
- [System context](system-context.md)
- [Container design](container-design.md)
- [Data ownership](data-ownership.md)
- [Memory and style](memory-and-style.md)
- [Retrieval and prompting](retrieval-and-prompting.md)
- [Feedback and evaluation](feedback-and-evaluation.md)
- [Multi-tenancy](multitenancy.md)
- [Channel connectors](channel-connectors.md)
- [Sales and support workflows](sales-and-support-workflows.md)
- [Safety, compliance, and audit](safety-compliance-and-audit.md)
- [Deployment evolution](deployment-evolution.md)
- [Build vs buy](build-vs-buy.md)
- [Roadmap](roadmap.md)
- [Open decisions](open-decisions.md)

Accepted decisions are recorded in [ADR](adr/).
