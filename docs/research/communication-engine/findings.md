# Findings

## Five primary findings

1. **Style, identity, memory, and workflow are different state.** soul.md and
   SillyTavern show the value of separate identity/style artifacts; Graphiti,
   Mem0, and LangMem show memory mutation; Chatwoot/SalesGPT show business state.
   Combining them in one "agent memory" destroys ownership and evaluation.

2. **Provenance is a product primitive, not metadata decoration.** Graphiti
   preserves episodes behind facts; the current system preserves human vs AI
   message authorship. This must extend through imports, compilation, retrieval,
   prompt plans, generation, approval, and delivery.

3. **Prompt-time adaptation is the correct v1 mechanism.** It is inspectable,
   correctable, per-contact, and provider-independent. Fine-tuning projects show
   future experimental value but poor deletion, rollback, and incremental
   correction properties.

4. **A graph database is not justified yet.** Graphiti solves real temporal and
   multi-hop problems, but PostgreSQL can hold typed facts/events/relationship
   observations, validity intervals, FTS, and optional pgvector at far lower
   operational cost.

5. **Reliable communication is mostly deterministic orchestration around an
   LLM.** Opt-out, approval, stage transitions, frequency caps, idempotency,
   quiet hours, send dedupe, retries, and audit cannot be delegated to a prompt.

## Contradictions resolved

- "Memory API" products often mean logical scoped retrieval, not secure
  multi-tenancy.
- "Self-hosted" ranges from a library to a large database/model stack; it does
  not imply low operations.
- "Learns style" ranges from Markdown curation to prompt examples to LoRA. These
  mechanisms have materially different provenance and deletion properties.
- A channel integration is not a channel abstraction. The abstraction must
  normalize identity, capability, message IDs, threading, delivery, limits, and
  retries.

## What is differentiated

The individual mechanisms are not novel. The distinct combination is:

- operator/team/brand identity layering;
- evidence-derived, versioned style with per-contact adaptation;
- provenance-safe facts and relationship history;
- deterministic business workflow and communication policy;
- approval modes and audited delivery across channels;
- incremental learning that never promotes AI output to human evidence.
