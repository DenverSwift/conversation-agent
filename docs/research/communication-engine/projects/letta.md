# Letta / MemGPT and LettaBot

## Snapshot

| Field | Value |
| --- | --- |
| Repositories | [letta-ai/letta](https://github.com/letta-ai/letta), [letta-ai/lettabot](https://github.com/letta-ai/lettabot) |
| Commits | Letta [`b76da9092518cbaa2d09042e52fdcbde69243e18`](https://github.com/letta-ai/letta/tree/b76da9092518cbaa2d09042e52fdcbde69243e18); LettaBot [`99c3b5dd73550fe0a4eac2ee31b1c3229ca9e550`](https://github.com/letta-ai/lettabot/tree/99c3b5dd73550fe0a4eac2ee31b1c3229ca9e550) |
| Researched | 2026-07-25 |
| License | Apache-2.0 (both) |
| Primary language | Python (Letta), TypeScript (LettaBot) |
| Activity | Active ecosystem; researched LettaBot commit 2026-05-03 |
| Delivery | Local/cloud agent platform plus multi-channel gateway |
| Maturity | Substantial; architecture currently transitioning |
| Purpose | Stateful agents with editable memory and channel access |

## Architecture

The specified `letta` repository now explicitly describes itself as the legacy
V1 API server; new development moved to Letta Agent/App Server
([README](https://github.com/letta-ai/letta/blob/b76da9092518cbaa2d09042e52fdcbde69243e18/README.md)).
The researched server is nevertheless a useful implementation: SQLAlchemy
models/services, REST routers, jobs, prompts, agent execution, and context
management form a large modular monolith.

`AgentState` binds system prompt, model configuration, tools, memory blocks,
message state, and sources
([schema](https://github.com/letta-ai/letta/blob/b76da9092518cbaa2d09042e52fdcbde69243e18/letta/schemas/agent.py)).
LettaBot is a separate gateway with a `ChannelAdapter` interface, channel
factories, session management, access control, batching, and dedupe utilities
([channel types](https://github.com/letta-ai/lettabot/blob/99c3b5dd73550fe0a4eac2ee31b1c3229ca9e550/src/channels/types.ts)).

## Identity and Style

Editable labeled blocks can carry persona and human data; blocks can be shared
between agents. Letta does not natively compile a source-provenanced style
profile or model per-contact variation. Identity is agent-centric rather than
workspace/company/operator/contact-centric.

## Memory Model

Core memory blocks live in the prompt. Recall memory is message history.
Archival passages are searched outside the immediate context. Blocks have
history and attachment relationships
([block schema](https://github.com/letta-ai/letta/blob/b76da9092518cbaa2d09042e52fdcbde69243e18/letta/schemas/block.py)).
This tiering is strong, but it mixes editable agent state with memory concepts
that our product should keep separately owned.

## Retrieval

Archival search is embedding-based with organization-filtered persistence.
Core blocks are always in context. Agents can use tools to search/write memory.
There is no native business-aware fusion across style examples, facts, policy,
relationship observations, and workflow state.

## Prompt Construction

The agent compiles memory blocks, sources, tool rules, archival/recall counts,
and system text, and skips rebuild when memory is unchanged
([base agent](https://github.com/letta-ai/letta/blob/b76da9092518cbaa2d09042e52fdcbde69243e18/letta/agents/base_agent.py);
[compiler helper](https://github.com/letta-ai/letta/blob/b76da9092518cbaa2d09042e52fdcbde69243e18/letta/services/helpers/agent_manager_helper.py)).
Context-window calculators and compaction provide a mature reference.

## Feedback and Evaluation

Agent traces and tool execution are observable, but Good/Bad/Fix review and
human approval before a channel send are not first-class Letta domain states.
Memory-writing agents also need source-policy constraints to avoid self-training.

## Multi-Tenancy

The server consistently stores and filters `organization_id`; identities,
agents, passages, sources, and traces use it. This is closer to multi-tenancy
than most projects, though an implementation audit still cannot equate service
filters with database RLS. LettaBot is primarily local/multi-agent, not a SaaS
tenant boundary.

## Business Workflow Support

Letta supports general tools, cron, and agent state, but not lead stages,
campaign caps, opt-outs, truthful-offer policies, or a sales/support state
machine. LettaBot's channel access controls, pairing, group gating, message
splitting, and adapter contract are valuable
([factory](https://github.com/letta-ai/lettabot/blob/99c3b5dd73550fe0a4eac2ee31b1c3229ca9e550/src/channels/factory.ts)).

## Deployment and Operations

The researched server is operationally heavy (PostgreSQL, migrations, workers,
model/tool integrations). LettaBot can run locally or against Letta cloud.
Architecture transition and cloud/local surface differences create migration
risk. Apache-2.0 permits direct reuse.

## Strong Ideas

- Editable, labeled, shareable in-context memory blocks.
- Explicit core/recall/archival context tiers.
- Skip prompt rebuild when compiled memory is unchanged.
- Channel adapters with access control, batching, splitting, and dedupe.
- Organization ownership on persisted entities.

## Weaknesses

- The specified server is legacy and the current product surface is moving.
- Agent-centric state is not a substitute for our communication domain.
- High operational and conceptual complexity.
- No native business approval/opt-out/campaign policy.

## Relevance to conversation-agent

Adapt the context tiers and LettaBot adapter boundary. Do not adopt Letta as the
product's aggregate root or require it for v1. Preserve the existing lightweight
style runtime and introduce channel ports in our own domain.

## Decision

- **Adapt now:** channel adapter contract and context-tier concepts.
- **Use as reference:** blocks, prompt rebuild detection, organization filters.
- **Revisit later:** Letta Agent SDK as an execution backend.
- **Reject for now:** replacing the product core with Letta.

## Evidence

- [Legacy/current status](https://github.com/letta-ai/letta/blob/b76da9092518cbaa2d09042e52fdcbde69243e18/README.md)
- [Agent state schema](https://github.com/letta-ai/letta/blob/b76da9092518cbaa2d09042e52fdcbde69243e18/letta/schemas/agent.py)
- [Prompt compilation](https://github.com/letta-ai/letta/blob/b76da9092518cbaa2d09042e52fdcbde69243e18/letta/services/helpers/agent_manager_helper.py)
- [LettaBot channel contract](https://github.com/letta-ai/lettabot/blob/99c3b5dd73550fe0a4eac2ee31b1c3229ca9e550/src/channels/types.ts)
- [LettaBot dedupe cache](https://github.com/letta-ai/lettabot/blob/99c3b5dd73550fe0a4eac2ee31b1c3229ca9e550/src/utils/dedupe-cache.ts)
- [Letta license](https://github.com/letta-ai/letta/blob/b76da9092518cbaa2d09042e52fdcbde69243e18/LICENSE)
