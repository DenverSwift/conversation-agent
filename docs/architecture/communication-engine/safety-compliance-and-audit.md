# Safety, Compliance, and Audit

## Policy gates

Policy runs before generation to avoid unnecessary private-data use and again
immediately before delivery because consent, time, campaign status, or message
content may have changed.

Required controls:

- do-not-contact and opt-out, including workspace and identity scope;
- lawful-basis or consent metadata where applicable;
- quiet hours, frequency caps, campaign limits, and channel rate limits;
- channel/platform restrictions and content/attachment constraints;
- truthful-claims and prohibited-content checks;
- sensitive-topic and low-confidence escalation;
- approval-mode enforcement and approval expiration;
- workspace, campaign, connector, and global auto-send kill switches.

The product must not claim that AI-generated text was authored personally by a
human when that would be deceptive. Exact disclosure requirements remain a
jurisdiction- and channel-specific product decision.

## Audit model

`AuditEvent` is append-only and records workspace, actor or service principal,
action, target type and ID, timestamp, request/correlation ID, reason, policy
version, outcome, and a redacted change summary. High-risk reads, exports,
support access, policy changes, approvals, sends, deletions, and credential
changes are audited.

Message content remains in the owning message store; the audit log references
it and stores hashes or limited redacted metadata. This avoids copying private
content into an indefinite log. Audit integrity can later use hash chaining or
external immutable retention if regulation requires it.

## Data lifecycle

- Classify fields by purpose and sensitivity.
- Encrypt transport, database, object storage, and backups.
- Keep connector tokens in a secret manager with rotation and least privilege.
- Apply per-workspace retention to messages, attachments, prompts, model
  traces, memories, exports, and backups.
- Export and deletion operate across primary data, search indexes, object
  storage, jobs, and provider-side data.
- Derived facts and style evidence retain source links so a deleted or
  disallowed source can be invalidated and recompiled.
- Redact logs and traces by default; diagnostics use explicit time-limited
  access.

## Threats and mitigations

Prompt injection in inbound text is untrusted data, never instruction. Retrieved
content is labeled and bounded. Tools are allow-listed by workflow, with
server-side parameters and no arbitrary execution. Egress is restricted.
Attachments are scanned before use. All automated sends require a persisted
policy decision, and delivery workers cannot generate or modify content.

This document is an engineering control baseline, not legal advice. Before a
production sales pilot, counsel and platform specialists must review target
jurisdictions, channel terms, consent, retention, recording, automated
decision-making, and disclosure.
