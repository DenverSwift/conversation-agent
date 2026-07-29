# Product Scope

## Product

A multi-user, multi-tenant engine that drafts, approves, sends, receives, and
learns from business communication while preserving operator/company voice,
contact context, workflow goals, privacy, and auditability.

## v1 outcomes

- Telegram-first sales/support pilot without Telegram-dependent core types.
- Workspace membership and operator/company identities.
- Contact, conversation, message, and immutable provenance.
- Draft-only and approval-required modes; narrowly allow-listed auto-send later.
- Lead/support workflow state, follow-up timers, escalation, and opt-out.
- Provider-neutral style compilation, retrieval, prompt plan, and generation.
- Good/Bad/Fix/Should-not-reply feedback and evaluation.
- Durable inbox/outbox, retries, idempotency, rate/frequency/quiet-hour policy.

## v1 domain entities

Required: `Workspace`, `UserPrincipal`, `Membership`,
`CommunicationIdentity`, `StyleProfileVersion`, `ConnectorAccount`, `Contact`,
`Conversation`, `Message`, `MessageProvenance`, `CommunicationPolicy`,
`BusinessObjective`, `PlaybookVersion`, `WorkflowInstance`,
`WorkflowTransition`, `Campaign`, `CampaignEnrollment`, `GeneratedReply`,
`PromptPlan`, `Approval`, `Feedback`, `EvaluationCase`, `AuditEvent`,
`InboxEvent`, and `OutboxDelivery`.

Represent as typed records rather than independent engines:
`Fact`, `Event`, `RelationshipObservation`, `StyleEvidence`, and
`Example`. Represent channel as a connector type plus capability snapshot, lead
stage as workflow state, and prompt version as fields on `PromptPlan`.
Personal/operator/team/company/brand voices are layers or variants of
`CommunicationIdentity`, not separate storage engines.

Postpone as standalone concepts: Relationship Engine, Goal Engine, graph
entities, fine-tuned model, critic agent, CQRS read model, and event-sourced
aggregate.

## Non-goals

- autonomous lead sourcing or scraping;
- bulk spam or ToS circumvention;
- undisclosed impersonation;
- full CRM, marketing automation suite, or help desk;
- general-purpose agent platform;
- training a model in this architecture phase.

## Success constraints

No cross-tenant data, no duplicate send, immediate opt-out, every automated
action auditable, AI text never treated as human evidence, and every outbound
message attributable to an identity, policy decision, prompt plan, model, and
approval mode.
