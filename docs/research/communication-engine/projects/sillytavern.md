# SillyTavern

## Snapshot

| Field | Value |
| --- | --- |
| Repositories | [SillyTavern](https://github.com/SillyTavern/SillyTavern), [Docs](https://github.com/SillyTavern/SillyTavern-Docs) |
| Commits | App [`8172dcd0ee672d3cd9a5e5f7af134f91a45cd2b8`](https://github.com/SillyTavern/SillyTavern/tree/8172dcd0ee672d3cd9a5e5f7af134f91a45cd2b8); docs [`70e5e4d3c239253fca4692fe82e3936cb9c4b1b1`](https://github.com/SillyTavern/SillyTavern-Docs/tree/70e5e4d3c239253fca4692fe82e3936cb9c4b1b1) |
| Researched | 2026-07-25 |
| License | AGPL-3.0 |
| Primary language | JavaScript |
| Activity | Active; researched app commit dated 2026-07-07 |
| Delivery | Self-hosted single-user-oriented web application |
| Maturity | Mature community application |
| Purpose | Model-agnostic character chat with detailed prompt/context controls |

## Architecture

SillyTavern is a large Node/browser monolith with server endpoints, a rich
client, local data, provider adapters, extensions, presets, and prompt managers.
Character-card parsing is isolated in
[`src/character-card-parser.js`](https://github.com/SillyTavern/SillyTavern/blob/8172dcd0ee672d3cd9a5e5f7af134f91a45cd2b8/src/character-card-parser.js);
world info, author notes, and prompt order are separate client modules.

Data flow is: character card + persona + scenario + examples + activated lore +
author note + chat history + instruct/provider preset -> budgeted prompt ->
model. It is an excellent prompt laboratory, not a business backend.

## Identity and Style

Character cards include description, personality, scenario, first message,
example dialogue, system prompt, alternative greetings, and a character book.
The parser handles multiple card formats. Persona is curated rather than
compiled from provenance-aware human evidence; per-contact adaptation is not a
native concept.

## Memory Model

Chat history, summaries/extensions, and lorebooks supply continuity. Lore entries
activate by keywords and configurable depth/order
([world-info source](https://github.com/SillyTavern/SillyTavern/blob/8172dcd0ee672d3cd9a5e5f7af134f91a45cd2b8/public/scripts/world-info.js)).
This is contextual lore, not factual CRUD, temporal truth, or relationship
memory.

## Retrieval

World info is primarily lexical/regex activation with priorities, recursion, and
token limits. Extensions may add vector search. The strong idea is not the
retrieval algorithm but explicit position, depth, priority, and budget controls.

## Prompt Construction

Prompt construction is the project's strongest area. `PromptManager` exposes
ordered prompt entries
([source](https://github.com/SillyTavern/SillyTavern/blob/8172dcd0ee672d3cd9a5e5f7af134f91a45cd2b8/public/scripts/PromptManager.js)).
Author's Note can be inserted at a selected depth
([source](https://github.com/SillyTavern/SillyTavern/blob/8172dcd0ee672d3cd9a5e5f7af134f91a45cd2b8/public/scripts/authors-note.js)).
Users can inspect and tune presets, context, negative prompts, and examples.

## Feedback and Evaluation

Regeneration, editing, swipes, and community presets provide informal feedback.
There is no durable Good/Bad/Fix domain, provenance-safe learning, experiment
registry, or business KPI evaluation.

## Multi-Tenancy

Not designed as a multi-tenant SaaS communication engine. User data separation
and authentication are insufficient as our target workspace model.

## Business Workflow Support

Absent. Characters and group chats do not represent campaigns, lead stages,
opt-outs, approvals, or audited sends.

## Deployment and Operations

Self-hosting is straightforward but the UI/runtime is large. AGPL-3.0 makes
copying server/client code into a proprietary hosted product a legal decision;
architectural ideas and interoperable card formats are safer reference points.

## Strong Ideas

- Character cards combine rules and example dialogue.
- Prompt order, insertion depth, activation, and token budget are inspectable.
- Lorebooks separate always-on identity from dynamically activated context.
- Negative prompts and anti-patterns are first-class.

## Weaknesses

- Curated roleplay persona is not evidence-derived business identity.
- No provenance, factual truth, multi-tenancy, workflow, or audit guarantees.
- High UI complexity and AGPL obligations.

## Relevance to conversation-agent

Adapt the prompt-plan inspector, explicit section ordering, example dialogue,
negative evidence, and dynamic activation concepts. Do not adopt the application
or treat roleplay continuity as business memory.

## Decision

- **Adapt now:** inspectable prompt plan and section budgets.
- **Use as reference:** character-card/example/lore concepts.
- **Reject:** direct code reuse without AGPL review and business-core adoption.

## Evidence

- [Character-card parser](https://github.com/SillyTavern/SillyTavern/blob/8172dcd0ee672d3cd9a5e5f7af134f91a45cd2b8/src/character-card-parser.js)
- [Prompt manager](https://github.com/SillyTavern/SillyTavern/blob/8172dcd0ee672d3cd9a5e5f7af134f91a45cd2b8/public/scripts/PromptManager.js)
- [World info retrieval](https://github.com/SillyTavern/SillyTavern/blob/8172dcd0ee672d3cd9a5e5f7af134f91a45cd2b8/public/scripts/world-info.js)
- [Author's Note insertion](https://github.com/SillyTavern/SillyTavern/blob/8172dcd0ee672d3cd9a5e5f7af134f91a45cd2b8/public/scripts/authors-note.js)
- [AGPL-3.0 license](https://github.com/SillyTavern/SillyTavern/blob/8172dcd0ee672d3cd9a5e5f7af134f91a45cd2b8/LICENSE)
