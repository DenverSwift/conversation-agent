# Technology Landscape

| Area | State of the art in reviewed sources | Product decision |
| --- | --- | --- |
| Persona/identity | Editable files/cards are well understood ([soul.md](projects/soul-md.md), [SillyTavern](projects/sillytavern.md)) | Adapt layered identity artifacts |
| Style extraction | Manual/LLM compilation and fine-tuning exist; provenance is weak | Keep our incremental provenance-aware compiler |
| Digital twins | End-to-end local training exists ([Second Me](projects/second-me.md), [WeClone](projects/weclone-clonellm.md)) | Postpone weights; avoid clone framing |
| Character systems | Prompt order, examples, lore, negative patterns are mature | Adapt prompt plan and activation |
| Memory frameworks | CRUD/extraction/providers are mature ([Mem0](projects/mem0.md), [LangMem](projects/langmem.md)) | Build a port; simple internal store first |
| Temporal knowledge | Graphiti has explicit validity/provenance | Adapt relational temporal fields; graph later |
| Retrieval | Vector, lexical, graph, fusion, and reranking are solved primitives | PostgreSQL FTS first; evaluate pgvector |
| Prompt orchestration | Letta/SillyTavern expose useful tiers and budgets | Own a provider-neutral versioned composer |
| Sales agents | Stage prompts exist, but safety/operations are incomplete | Own deterministic state and policy |
| Support agents | Chatwoot-like inbox/assignment is mature | Integrate or adapt domain; do not reinvent UI blindly |
| CRM automation | Established external systems are stronger systems of record | Integrate; do not build a full CRM in v1 |
| Human feedback | UI patterns exist; safe evidence promotion is less common | Preserve and generalize Good/Bad/Fix |
| Evaluation | Memory benchmarks exist; communication/business evaluation is fragmented | Build a layered evaluation registry |
| Channels | LettaBot shows good adapter/access/dedupe patterns | Own normalized channel ports |
| Compliance/safety | No reviewed clone/memory project supplies the complete layer | Core proprietary development |

## Well solved

Provider adapters, embeddings, vector stores, FTS, character/example formats,
memory CRUD, prompt section ordering, and basic channel connectors.

## Partially solved

Temporal truth, per-user profiles, background consolidation, prompt
optimization, multi-channel session routing, and memory evaluation.

## Product-owned work

Tenant-safe communication data, layered identity/style selection, message
provenance, deterministic sales/support workflows, approval/policy/send
controls, unified audit, and business-aware retrieval/prompt plans.

## Reinvention traps

Building a vector database, graph engine, CRM, general agent framework, model
gateway, or durable workflow engine before existing components fail measured
requirements.
