# Core Domain Model

```mermaid
erDiagram
    WORKSPACE ||--o{ MEMBERSHIP : has
    USER_PRINCIPAL ||--o{ MEMBERSHIP : joins
    WORKSPACE ||--o{ COMMUNICATION_IDENTITY : owns
    COMMUNICATION_IDENTITY ||--o{ STYLE_PROFILE_VERSION : compiles
    WORKSPACE ||--o{ CONNECTOR_ACCOUNT : configures
    WORKSPACE ||--o{ CONTACT : owns
    CONTACT ||--o{ CONVERSATION : participates
    CONNECTOR_ACCOUNT ||--o{ CONVERSATION : carries
    CONVERSATION ||--o{ MESSAGE : contains
    MESSAGE ||--|| MESSAGE_PROVENANCE : has
    CONVERSATION ||--o{ GENERATED_REPLY : proposes
    GENERATED_REPLY ||--|| PROMPT_PLAN : records
    GENERATED_REPLY ||--o| APPROVAL : requires
    GENERATED_REPLY ||--o{ FEEDBACK : receives
    GENERATED_REPLY ||--o{ EVALUATION_CASE : evaluates
    CONTACT ||--o{ FACT : described_by
    CONTACT ||--o{ EVENT : described_by
    CONTACT ||--o{ RELATIONSHIP_OBSERVATION : described_by
    MESSAGE ||--o{ FACT : supports
    MESSAGE ||--o{ EVENT : supports
    MESSAGE ||--o{ STYLE_EVIDENCE : supports
    WORKSPACE ||--o{ COMMUNICATION_POLICY : enforces
    WORKSPACE ||--o{ BUSINESS_OBJECTIVE : defines
    WORKSPACE ||--o{ PLAYBOOK_VERSION : defines
    CONTACT ||--o{ WORKFLOW_INSTANCE : follows
    PLAYBOOK_VERSION ||--o{ WORKFLOW_INSTANCE : governs
    WORKFLOW_INSTANCE ||--o{ WORKFLOW_TRANSITION : records
    WORKSPACE ||--o{ CAMPAIGN : owns
    BUSINESS_OBJECTIVE ||--o{ CAMPAIGN : pursues
    PLAYBOOK_VERSION ||--o{ CAMPAIGN : configures
    COMMUNICATION_IDENTITY ||--o{ CAMPAIGN : speaks_as
    CAMPAIGN ||--o{ CAMPAIGN_ENROLLMENT : enrolls
    CONTACT ||--o{ CAMPAIGN_ENROLLMENT : joins
    CONNECTOR_ACCOUNT ||--o{ INBOX_EVENT : receives
    INBOX_EVENT ||--o| MESSAGE : creates
    MESSAGE ||--o| OUTBOX_DELIVERY : delivers
    WORKSPACE ||--o{ AUDIT_EVENT : audits
```

## Aggregate boundaries

- **Conversation:** messages and channel thread identity; it never owns facts or
  style rules.
- **Generation:** prompt plan, generated reply, model metadata, safety result,
  and approval; immutable after delivery except review state.
- **Identity/style:** editable identity plus immutable compiled versions.
- **Memory:** facts/events/relationship observations with source links and
  validity.
- **Workflow:** explicit state and transitions; no state is inferred only from
  an LLM message.
- **Delivery:** inbox/outbox attempts and external IDs.

## Provenance enums

`origin`: `contact`, `human_operator`, `ai_generated`, `imported`,
`system_generated`.

`evidence_eligibility`: `human_style_positive`, `human_fix_positive`,
`negative`, `context_only`, `evaluation_only`, `ineligible`.

`approval_mode`: `draft_only`, `approval_required`, `auto_send_allowlisted`,
`human_only`.

## Temporal fields

Facts and relationship observations use `observed_at`, `valid_from`,
`valid_until`, `recorded_at`, `supersedes_id`, `confidence`, and
`source_message_id`. This borrows Graphiti's temporal semantics without making a
graph database mandatory.
