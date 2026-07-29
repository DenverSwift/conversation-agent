# Build vs Buy

## Decision criteria

Prefer ownership where behavior defines product trust: tenant isolation,
provenance, consent, policy, workflow state, approvals, delivery idempotency,
audit, and the exact separation of human evidence from AI output. Adopt or buy
commodities when they can sit behind a narrow contract and support export,
deletion, observability, and tenant guarantees.

`Build` means own the domain contract and implementation. `Adopt` means use a
self-hosted library or infrastructure component. `Buy` means use a managed
service behind an adapter. `Defer` means keep the simpler baseline until
measured evidence changes the decision.

## Component decisions

| Capability | Decision | Initial choice | Migration trigger |
| --- | --- | --- | --- |
| Style compiler | Build by extending AA.2 | Incremental provider-neutral compiler | Replace internals only if invariants and fixtures still pass |
| Style evidence store | Build | PostgreSQL source records and immutable versions | Separate storage only for measured volume/isolation |
| Factual memory | Build contract and simple store | Source-linked relational observations | External provider passes lineage/deletion benchmark |
| Episodic memory | Build | Messages, events, summaries as derived records | Scale requires separate archive/index |
| Temporal relationship memory | Build relational baseline | Valid/observed/superseded fields | Graphiti pilot proves multi-hop quality gain |
| Lexical search | Adopt | PostgreSQL FTS | Search scale or language quality exceeds it |
| Vector search | Defer, then adopt | pgvector in the same tenant database | Representative evaluation proves recall gain |
| Hybrid retrieval/reranking | Defer, then build composition | Weighted FTS/vector candidates | Quality gain covers cost and latency |
| Prompt composer | Build | Versioned `PromptPlan` and provider renderer | No external component may hide evidence lineage |
| Model inference | Buy and abstract | Multiple providers behind one contract | Privacy/cost may justify self-hosted models |
| Workflow engine | Build simple state machine; defer platform | PostgreSQL transitions and jobs | Temporal when compensation/visibility is demonstrably insufficient |
| Job queue | Adopt database primitives first | Transactional outbox, due jobs, `SKIP LOCKED` | Broker only for measured throughput/fan-out |
| CRM | Integrate | Export/import and narrow APIs | Never become full CRM without a new product decision |
| Channel connectors | Build domain ports; reuse SDKs | Telegram legacy adapter | Add adapters only with platform approval and capability tests |
| Evaluation | Build product-specific registry | Fixtures, replay, shadow, outcome metrics | Use external judge/observability tools as replaceable executors |
| Observability | Adopt | OpenTelemetry-compatible traces, metrics, logs | Managed vendor chosen by privacy/residency/cost |
| Analytics | Start with operational queries | PostgreSQL aggregates and redacted events | Warehouse after volume and stakeholder need |
| Human approval UI | Start small or integrate | Focused review queue; assess Chatwoot-style inbox | Build richer UI only after operator workflow is learned |
| Secrets/object storage | Adopt or buy | Established secret manager and object store | Provider change through infrastructure adapter |
| Memory provider | Defer | Internal authoritative memory | Mem0/Supermemory adapter passes acceptance tests |
| Graph database | Defer | No graph in v1 | Measured temporal/multi-hop failures |
| Fine-tuning | Reject for v1 | Prompt-time conditioning | Separate consented experiment with deletion plan |

## Trade-off assessment

| Option | Control | Cost and time to market | Privacy and lock-in | License and operations | Migration complexity |
| --- | --- | --- | --- | --- | --- |
| Product-owned domain core | Highest control over invariants | More initial engineering; fastest iteration after product fit | Private data stays under our policy; low vendor lock-in | Our maintenance burden | Moderate if contracts and migrations are versioned |
| PostgreSQL/FTS/pgvector libraries | High data control | Low license cost and fast start | Self-hosted; portable SQL, some extension coupling | Permissive ecosystem; moderate database operations | Low from FTS to pgvector in the same store |
| Managed model/embedding service | Low model control, high model choice | Fastest start; variable token cost | Private content leaves boundary; provider and residency risk | Contract/ToS review; low infrastructure operations | Moderate through a strict request/response adapter |
| Self-hosted model | High runtime control | Slower start and hardware/staff cost | Strong locality; model/license constraints | Significant capacity, patching, and evaluation burden | Moderate if the generation contract is stable |
| Managed memory/search service | Medium control | Fast feature access; recurring usage cost | Highest lineage, deletion, residency, and provider-lock risk | Terms may differ from OSS repo; low-to-medium operations | High if provider IDs become domain IDs |
| Self-hosted memory framework | Medium-high control | Integration faster than building advanced retrieval | Tenant security remains our responsibility | License review plus databases/workers | Medium behind authoritative internal records |
| Workflow platform | Medium control | Fast for complex durable orchestration, excessive for v1 | Workflow state couples to platform semantics | More infrastructure or service cost | Medium-high; history migration is difficult |
| CRM/help-desk integration | Low control over external records/UI | Much faster than rebuilding mature workflows | Customer data crosses systems; API/vendor limits | Commercial/OSS terms and connector maintenance | Medium through synchronized external IDs |
| Custom approval UI | Highest workflow control | Slower than integration until needs stabilize | Private data remains in product | Frontend and accessibility burden | Low domain migration; UI can be replaced |

The controlling principle is to keep authoritative IDs, tenant ownership,
provenance, policy decisions, and source lineage inside the communication
engine. A bought component may execute or index work but cannot become the only
place those facts exist.

## Landscape-specific choices

- Borrow LangMem's typed consolidation and prompt-optimization patterns without
  requiring LangGraph.
- Consider Mem0 or Supermemory only as a memory adapter, not tenant authority.
- Consider Graphiti only for measured temporal relationship retrieval.
- Borrow SillyTavern's prompt layering and negative-example UX, not its backend.
- Use LettaBot's adapter, access-control, batching, and deduplication patterns.
- Reject the Podisen runtime: in-memory state, unsafe defaults, logging risk,
  and unclear license.
- Reject WeClone as runtime: AGPL and fine-tuning do not match the product
  boundary; study its grouping and PII pipeline only.
- Borrow CloneLLM's typed profile and retrieval schemas, not its in-memory
  durability or concealment behavior.

## Adapter acceptance tests

Any external memory, search, channel, or model component must pass tenant
isolation, source attribution, deterministic deletion/invalidation, export,
timeouts, retries, cost limits, trace redaction, and provider-replacement tests.
If it cannot return evidence IDs and source lineage, it may assist ranking but
cannot become the authoritative memory store.

License and hosted-product terms require a fresh review before adoption. This
research records repository licenses and observable behavior, not a legal
clearance.
