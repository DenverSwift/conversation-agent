# Channel Connectors

## Contract

Core modules use a channel-neutral `MessageEnvelope`. It contains workspace,
connector account, external conversation/message IDs, direction, sender and
recipient identities, timestamps, text/media references, reply/thread
references, and normalized delivery metadata. Telegram entities remain in the
Telegram adapter.

Each connector implements receive normalization, send, capability discovery,
delivery-status handling, and identity/account mapping. Capability flags cover
editing, deletion, reactions, threads, attachments, maximum size, rate limits,
and explicit opt-out signals.

## Inbound flow

```mermaid
sequenceDiagram
    participant P as Channel platform
    participant A as Channel adapter
    participant I as Inbox
    participant C as Conversation module
    participant W as Workflow and policy
    participant G as Generation

    P->>A: Platform event
    A->>I: Insert normalized event with unique external key
    alt Duplicate
        I-->>A: Existing processing result
    else New
        I->>C: Resolve account, contact, conversation
        C->>C: Persist message and provenance
        C->>W: Emit message-received event
        W->>W: Detect opt-out and choose next action
        opt Draft reply is allowed
            W->>G: Request generation
        end
        I->>I: Mark processed
    end
```

The inbox is committed before processing. Redelivery is normal and handlers are
idempotent.

## Outbound flow

```mermaid
sequenceDiagram
    participant W as Workflow
    participant P as Policy
    participant G as Generation
    participant H as Human approval
    participant O as Outbox
    participant A as Channel adapter
    participant X as Channel platform

    W->>P: Check action eligibility
    P-->>W: Allow, require approval, or deny
    W->>G: Build prompt plan and draft
    G->>H: Persist generated reply
    alt Approval required
        H->>H: Approve, edit, reject, or expire
    end
    H->>P: Pre-send recheck
    P-->>O: Authorized immutable delivery request
    O->>A: Send with idempotency key
    A->>X: Platform API call
    X-->>A: External message ID or retryable error
    A-->>O: Delivered, retry scheduled, or failed
    O->>O: Persist attempt and status
```

## Idempotency and ordering

Inbound uniqueness is `(connector_account_id, external_event_id)` or a stable
platform-derived equivalent. Outbound uniqueness is a server-generated
delivery key bound to one approved content version. A retry cannot create a new
logical message. Per-conversation sequence numbers protect ordering where the
platform cannot.

Unknown send outcomes are reconciled by platform lookup when available and are
never blindly retried as a new message. Connector credentials are secret-store
references, not domain fields or logs.

## Migration

The existing Telethon agent becomes the first legacy adapter. The existing
Telegram Bot API trainer remains a separate feedback ingress until its actions
can use the same authenticated command and audit contracts.
