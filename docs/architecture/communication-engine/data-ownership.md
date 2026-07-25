# Data Ownership

| Data | Owner | Mutability | Notes |
| --- | --- | --- | --- |
| Workspace/membership | Tenancy | Controlled | RLS root |
| Identity definition | Identities | Editable/versioned | Operator/team/company layers |
| Compiled style | Identities | Immutable version | Derived from eligible evidence |
| Connector account/capabilities | Delivery | Controlled/versioned snapshot | Secrets referenced, never embedded |
| Contact/consent | Contacts | Controlled | Opt-out overrides all workflows |
| Conversation/message | Conversations | Append-mostly | External edits become revisions |
| Provenance | Conversations | Immutable | Never rewritten by feedback |
| Facts/observations | Memory | Versioned/superseded | Source-linked temporal projections |
| Objective/playbook/campaign | Workflows | Versioned configuration | Enrollment binds exact versions |
| Workflow state | Workflows | Transition-only | Optimistic lock/version |
| Prompt plan/reply | Generation | Immutable | Reproducibility record |
| Approval/feedback/evaluation | Approval/feedback | Append/update state | Human actor and rubric recorded |
| Inbox/outbox | Delivery | State machine | Unique idempotency keys |
| Audit | Audit | Append-only | Hash/retention options later |

## Privacy classes

- `secret`: API tokens and channel sessions; secret manager only.
- `private_content`: message text, prompts, corrections, memories; encrypted
  storage and restricted logs.
- `sensitive_metadata`: contact IDs, external IDs, delivery times.
- `operational`: aggregate counts, latency, safe error categories.

Git contains code, schemas, fixtures, and documentation only. Private imports,
exports, database dumps, sessions, compiled bundles, and evaluation results stay
outside Git, preserving the AA.2 rule.

## Deletion

Deletion is tenant-scoped and cascades through derived artifacts by source
links. Audit retains a tombstone/action record without deleted content. A style
or memory rebuild removes contributions from deleted sources; provider deletion
requests are tracked separately.
