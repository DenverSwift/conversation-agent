# Comparison Matrix

Codes: `N` native, `P` partial, `O` possible over primitives, `A` absent, `U`
unclear. Each project name links to its evidence-backed report.

## Identity, memory, and retrieval

| Project | Identity/persona | Style extraction | Per-contact | Factual | Episodic | Temporal | Relationship | Vector | Lexical | Graph | Hybrid |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [soul.md](projects/soul-md.md) | N | P | A | A | P | A | A | A | A | A | A |
| [LangMem](projects/langmem.md) | O | O | O | N | N | P | O | P | O | A | O |
| [Mem0](projects/mem0.md) | P | A | O | N | P | P | P | N | P | P | P |
| [Letta](projects/letta.md) | N | A | O | N | P | P | O | N | A | A | P |
| [Graphiti](projects/graphiti-zep.md) | O | A | O | N | N | N | N | N | N | N | N |
| [Supermemory](projects/supermemory.md) | P | A | O | N | P | P | P | N | U | P | N |
| [SillyTavern](projects/sillytavern.md) | N | A | A | P | P | A | P | P | N | A | P |
| [Second Me](projects/second-me.md) | N | N | A | N | P | P | O | N | P | P | P |
| [Podisen](projects/podisen.md) | P | N | A | A | A | A | A | A | A | A | A |
| [WeClone/CloneLLM](projects/weclone-clonellm.md) | N | P | A | P | A | A | A | N | A | A | P |

## Prompting, feedback, provenance, and tenancy

| Project | Prompt composer | Prompt versioning | Feedback | Corrections | Provenance | Incremental | Multi-tenancy | Self-hosting | Privacy |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [soul.md](projects/soul-md.md) | P | A | P | P | A | P | A | N | P |
| [LangMem](projects/langmem.md) | P | P | P | N | P | N | P | N | P |
| [Mem0](projects/mem0.md) | A | P | P | N | P | N | P | N | P |
| [Letta](projects/letta.md) | N | P | P | P | P | N | P | N | P |
| [Graphiti](projects/graphiti-zep.md) | A | P | A | O | N | N | P | N | P |
| [Supermemory](projects/supermemory.md) | P | U | P | P | P | N | P | N | U |
| [SillyTavern](projects/sillytavern.md) | N | P | P | P | P | P | A | N | P |
| [Second Me](projects/second-me.md) | P | P | P | P | P | P | A | N | N |
| [Podisen](projects/podisen.md) | P | A | A | A | A | A | A | P | A |
| [WeClone/CloneLLM](projects/weclone-clonellm.md) | P | A | P | P | P | P | A | N | P |

## Business, channels, operations, and recommendation

| Project | Business goals | Sales | Support | Channel abstraction | Telegram | Evaluation | Observability | License | Ops complexity | Recommended usage |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| [soul.md](projects/soul-md.md) | A | A | A | A | A | P | A | MIT | Low | Adapt artifacts |
| [LangMem](projects/langmem.md) | A | A | A | A | A | P | P | MIT | Medium | Memory/reference |
| [Mem0](projects/mem0.md) | A | A | A | A | A | N | P | Apache-2.0 | Medium-high | Pilot adapter later |
| [Letta](projects/letta.md) | O | O | O | N | N | P | N | Apache-2.0 | High | Adapt channels/context |
| [Graphiti](projects/graphiti-zep.md) | A | A | A | A | A | P | N | Apache-2.0 | High | Temporal reference |
| [Supermemory](projects/supermemory.md) | A | A | A | P | P | N | P | MIT | Medium/managed | Benchmark later |
| [SillyTavern](projects/sillytavern.md) | A | A | A | P | A | P | P | AGPL-3.0 | Medium | Prompt reference |
| [Second Me](projects/second-me.md) | A | A | A | P | P | P | P | Apache-2.0 | High | Onboarding reference |
| [Podisen](projects/podisen.md) | A | A | A | A | A | P | P | Unclear | Medium | Reject runtime |
| [WeClone/CloneLLM](projects/weclone-clonellm.md) | A | A | A | P | N/P | P | P | AGPL/MIT | High/low | Dataset/profile reference |
