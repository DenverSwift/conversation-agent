# Additional Projects

## Snapshot

| Project | Repository | Why included | Decision |
| --- | --- | --- | --- |
| Chatwoot | [chatwoot/chatwoot](https://github.com/chatwoot/chatwoot) | Omnichannel support inbox, assignment, contact/conversation ownership | Use as business-domain reference |
| SalesGPT | [filip-michalsky/SalesGPT](https://github.com/filip-michalsky/SalesGPT) | Explicit sales stages and tool orchestration | Use as a cautionary/reference prototype |
| Rasa | [RasaHQ/rasa](https://github.com/RasaHQ/rasa) | Durable conversational flows, NLU, handoff patterns | Revisit for deterministic workflow needs |
| Temporal | [temporalio/temporal](https://github.com/temporalio/temporal) | Durable timers, retries, cancellation, long-running workflows | Revisit at production scale |
| PostgreSQL FTS + pgvector | [pgvector/pgvector](https://github.com/pgvector/pgvector) | One operational store for relational, lexical, and vector retrieval | Adopt progressively |

The additional set was researched as architecture triangulation, not at the same
source-depth as the ten mandatory reports. No mandatory project was replaced.

## Architecture

Chatwoot demonstrates that contacts, inboxes/channels, conversations, messages,
assignments, teams, and accounts are durable business entities rather than
prompt metadata. Its models and migrations are a useful counterweight to
agent-centric frameworks
([conversation model](https://github.com/chatwoot/chatwoot/blob/develop/app/models/conversation.rb)).

SalesGPT expresses sales stages in an agent controller
([source](https://github.com/filip-michalsky/SalesGPT/blob/main/salesgpt/agents.py)),
but its prototype-level LLM orchestration should not own compliance or send
state. Rasa separates deterministic flows/actions from probabilistic language
behavior. Temporal separates durable execution history from business records;
it is useful only when PostgreSQL jobs/timers cease to be reliable enough.

## Identity and Style

None of these projects supplies our evidence-derived style layering. Chatwoot's
account/agent/contact ownership and Rasa's assistant configuration are relevant
boundaries; identity/style remains our domain.

## Memory Model

Chatwoot provides operational conversation history, not agent memory. Rasa
provides tracker state. Temporal provides workflow history, not user facts.
PostgreSQL can initially store facts/events/observations with explicit types and
validity fields.

## Retrieval

PostgreSQL full-text search plus pgvector supports a staged hybrid implementation
without a new database
([pgvector README](https://github.com/pgvector/pgvector/blob/master/README.md)).
It does not provide reranking or graph semantics automatically.

## Prompt Construction

SalesGPT and Rasa show how workflow state can constrain language generation.
They do not provide the provenance-aware layered prompt plan required here.

## Feedback and Evaluation

Rasa has testing/story tooling; Chatwoot has support metrics. These validate the
need for separate language-quality and business-outcome evaluation.

## Multi-Tenancy

Chatwoot's account-scoped domain is the strongest relevant reference in this
additional set. It still requires an independent security audit before copying
authorization assumptions.

## Business Workflow Support

Chatwoot natively covers assignment, status, inbox, contacts, and support
handoff. SalesGPT models sales stages. Temporal models reliable timers and
retries. Together they show why workflow state must not be hidden inside agent
memory.

## Deployment and Operations

Chatwoot/Rasa/Temporal are large platforms. Direct adoption would dominate the
small product. PostgreSQL/pgvector is the lowest-increment operational choice.

## Strong Ideas

- Model business conversation state relationally.
- Keep deterministic workflow transitions outside the LLM.
- Adopt durable workflow infrastructure only after measured need.
- Start hybrid search in the primary database.

## Weaknesses

- Each platform solves only one slice and can become an architectural center of
  gravity.
- SalesGPT is not a compliance-safe outbound engine.
- PostgreSQL hybrid search still needs application ranking and evaluation.

## Relevance to conversation-agent

These projects support a modular monolith: our own communication domain,
PostgreSQL storage/search, an outbox worker, and channel adapters. Chatwoot may
later be integrated as a support UI/CRM rather than copied.

## Decision

- **Adopt next:** PostgreSQL FTS; add pgvector only after embedding evaluation.
- **Adapt:** Chatwoot domain concepts and deterministic sales stages.
- **Revisit later:** Rasa and Temporal.
- **Reject for now:** wholesale platform adoption.

## Evidence

- [Chatwoot conversation domain](https://github.com/chatwoot/chatwoot/blob/develop/app/models/conversation.rb)
- [SalesGPT agents](https://github.com/filip-michalsky/SalesGPT/blob/main/salesgpt/agents.py)
- [Rasa repository](https://github.com/RasaHQ/rasa)
- [Temporal repository](https://github.com/temporalio/temporal)
- [pgvector implementation and PostgreSQL usage](https://github.com/pgvector/pgvector/blob/master/README.md)
