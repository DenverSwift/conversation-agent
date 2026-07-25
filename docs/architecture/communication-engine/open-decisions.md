# Open Decisions

These questions require product, legal, operational, or measured technical
evidence. They are not license to relax the documented safety defaults.

| Decision | Evidence needed | Default until decided |
|---|---|---|
| First customer and workflow | Interviews, volume, desired outcome, operator process | Telegram-first approval-required sales/support pilot |
| Target jurisdictions and legal basis | Counsel review and customer data map | No production outreach |
| AI disclosure policy | Counsel, channel terms, customer expectation testing | Do not deceptively imply personal human authorship |
| Supported Telegram integration | Platform and account-risk review | Preserve current adapter only in controlled pilot |
| Auto-send eligibility | Shadow metrics, error taxonomy, opt-out and complaint data | Disabled except explicit narrow allow-list |
| Workspace identity model | Product decisions on personal, team, and company voice | Separate operator and company communication identities |
| Memory retention | Customer requirements, privacy/legal review, deletion tests | Minimal retention with source-linked invalidation |
| Attachment and audio scope | Use cases, malware/privacy controls, channel limits | Text first |
| FTS versus pgvector | Representative retrieval benchmark | PostgreSQL FTS |
| External memory service | Isolation, lineage, deletion, latency, cost evaluation | Internal memory records |
| Graph memory | Multi-hop/temporal failure cases and operational capacity | No graph database |
| Workflow engine | Queue scale, compensation complexity, operational incidents | PostgreSQL jobs |
| Operator inbox | Build estimate versus integration constraints | Minimal product UI or existing Telegram trainer |
| Data residency and enterprise isolation | Customer demand and infrastructure design | Shared PostgreSQL with RLS |
| Model/provider policy | Quality, privacy, residency, cost, and failover benchmark | Provider-neutral contract, no single-provider domain fields |
| Fine-tuning | Explicit consent, deletion plan, stable quality gain | Disabled |

Before implementation, convert resolved decisions into ADRs or product
requirements. Record the decision owner, deadline, evidence, and affected
workspace/channel scope.
