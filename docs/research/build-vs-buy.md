# Build Vs Buy

Access date: 2026-07-28.

| Component | Decision | Rationale |
| --- | --- | --- |
| Telegram transport | Build on Telethon now; evaluate Telegram Business later | Current MVP needs personal account control. Telegram Business is safer for business inboxes but changes product assumptions. |
| Memory | Adopt open source or buy later | LangMem, Mem0, Letta, Graphiti/Zep, and Supermemory are stronger than a custom generic memory engine. |
| Style compiler | Build | The unique requirement is provenance-governed Matvey style from real evidence, Fix priority, and no AI replies as style evidence. |
| Relationship model | Build thin first, maybe adopt graph memory later | Graphiti/Zep can help, but project-specific relationship semantics need validation. |
| Retrieval | Adopt open source primitives, build ranking policy | Vector/keyword/graph retrieval can be adopted. Ranking contact examples and Fix corrections is project-specific. |
| Prompt composer | Build | Prompt priority, provenance, safety, and Telegram output constraints are specific to this agent. |
| Human behavior runtime | Build | No reviewed system provides confirmed interruption, delay, typing, splitting, reaction, and non-response policy for a personal Telegram account. |
| Sales planner | Defer | Business goals are not part of the local AA.2 runtime. |
| Support workflow | Buy or integrate later | respond.io and similar tools are stronger for team inboxes, routing, CRM, and handoff. |
| CRM integration | Buy/integrate later | CRM is commodity integration work unless Telegram-native outreach becomes central. |
| Feedback system | Build | Feedback is private, evidence-sensitive, and directly tied to style compilation. |
| Evaluation | Build minimal, adopt harnesses later | The project needs behavior/style regression tests first. |
| Observability | Build minimal local logs now; adopt later for teams | Current privacy model favors local logs. |

## Preliminary Decision

Build the unique behavior/style core. Adopt or buy memory, CRM, and support tooling only after the first vertical slice proves that Telegram style behavior is valuable and safe.
