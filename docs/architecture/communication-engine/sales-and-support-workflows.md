# Sales and Support Workflows

## Deterministic state, assisted text

Workflow state and allowed transitions are deterministic. The model may
classify a reply, extract a candidate fact, or draft text, but it cannot invent
a state transition outside the playbook. Each transition records previous and
next state, trigger, actor, policy decision, timestamp, and related message.

The initial sales stages are `discovered`, `imported`, `relevance_review`,
`enrichment_review`, `ready`, `first_outreach`, `awaiting_reply`, `follow_up`,
`qualification`, `objection`, `proposal`, `meeting`, `won`, `lost`,
`disqualified`, `opted_out`, and `escalated`. A support playbook can use a
separate state set behind the same transition contract. Contact discovery and
enrichment are external/manual inputs in v1: the engine records their source
and review status but does not scrape or autonomously source leads.

## Campaign and reply flow

```mermaid
flowchart TD
    Import["Consent-compatible contact import"] --> Review["Relevance review"]
    Review -->|Reject| Disqualified["Disqualified"]
    Review -->|Approve| Enrich["Optional enrichment with source and review"]
    Enrich --> Enroll["Campaign enrollment"]
    Enroll --> Eligibility{"Policy and schedule eligible?"}
    Eligibility -->|No| Wait["Wait or exclude with reason"]
    Eligibility -->|Yes| Draft["Generate first-outreach draft"]
    Draft --> Approval{"Approval mode"}
    Approval -->|Rejected| Stop["Stop or revise"]
    Approval -->|Approved or allow-listed| Send["Outbox delivery"]
    Send --> Await["Awaiting reply"]
    Await -->|Timer| FollowUp["Frequency-capped follow-up"]
    Await -->|Inbound| Classify["Classify intent and next allowed action"]
    Classify -->|Interested| Qualify["Qualification"]
    Classify -->|Objection| Objection["Objection handling draft"]
    Classify -->|Human needed| Escalate["Escalation"]
    Classify -->|Opt-out| OptOut["Opt-out"]
```

Campaigns define objective, identity, audience snapshot, playbook version,
approval mode, schedule, channel, frequency cap, and policy version. Enrollment
is independently pausable and records why a contact entered or left.

## Opt-out flow

```mermaid
sequenceDiagram
    participant M as Inbound message or operator
    participant P as Policy module
    participant W as Workflow
    participant O as Outbox
    participant A as Audit

    M->>P: Explicit or confirmed opt-out
    P->>P: Create do-not-contact record
    P->>W: Cancel active enrollments and timers
    W->>O: Cancel unsent deliveries
    P->>A: Record source, scope, actor, and timestamp
    P-->>M: Optional compliant acknowledgement only
```

Opt-out is synchronous and takes precedence over campaign state, model output,
and operator defaults. Re-entry requires a new auditable lawful basis or
explicit consent according to the workspace policy.

## Escalation flow

```mermaid
flowchart LR
    Trigger["Low confidence, sensitive topic, policy risk, complaint, or human request"]
    Trigger --> Case["Create escalation with reason and severity"]
    Case --> Freeze["Pause automation and pending deliveries"]
    Freeze --> Assign["Assign queue or operator"]
    Assign --> Context["Show messages, facts with sources, workflow, and draft"]
    Context --> Resolve["Human resolves and records outcome"]
    Resolve --> Resume{"Resume allowed?"}
    Resume -->|Yes| Policy["Re-evaluate policy and workflow"]
    Resume -->|No| Close["Close, opt-out, or retain manual-only mode"]
```

## Timers

Database-backed jobs are sufficient for v1. A job claims a due transition with
`SKIP LOCKED`, checks the current workflow version and policy, and writes the
next job in the same transaction. Temporal becomes relevant only when measured
volume, long-running compensation, or operational visibility exceeds this
model.
