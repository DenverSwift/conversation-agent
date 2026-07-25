# Second Me

## Snapshot

| Field | Value |
| --- | --- |
| Repository | [mindverse/Second-Me](https://github.com/mindverse/Second-Me) |
| Commit | [`d0e40251d9de61b3340b8d0d7d83150669f1885a`](https://github.com/mindverse/Second-Me/tree/d0e40251d9de61b3340b8d0d7d83150669f1885a) |
| Researched | 2026-07-25 |
| License | Apache-2.0 |
| Primary language | Python/TypeScript |
| Activity | Researched commit dated 2025-09-19; less recent than memory frameworks |
| Delivery | Local Docker application with optional network integration |
| Maturity | Complex research/product prototype |
| Purpose | Build a locally trained personal model and identity from user memories |

## Architecture

Second Me has frontend, Flask kernel/API, relational persistence, Chroma
embeddings, document processors, hierarchical L0/L1/L2 pipelines, training, and
network integration. L0 stores chunks/embeddings; L1 synthesizes biography,
topics, and clusters; L2 prepares data and performs LoRA/DPO training. The
training pipeline is visible under
[`lpm_kernel/L2`](https://github.com/mindverse/Second-Me/tree/d0e40251d9de61b3340b8d0d7d83150669f1885a/lpm_kernel/L2).

Data flow: local files -> parse/chunk/embed -> synthesize hierarchical identity
data -> generate training/preference data -> fine-tune/merge/serve model ->
role-aware chat.

## Identity and Style

Identity is a local personal model plus synthesized biography/knowledge.
Role records allow different interaction roles
([role service](https://github.com/mindverse/Second-Me/blob/d0e40251d9de61b3340b8d0d7d83150669f1885a/lpm_kernel/api/domains/kernel2/services/role_service.py)).
This is richer than a prompt profile but expensive to update, difficult to
attribute, and oriented to one person's digital self rather than operators,
brand voice, contacts, and company policies.

## Memory Model

Raw memories/documents, chunks/embeddings, L1 clusters/biography, and trained
weights are distinct stages. Versioned L1 outputs exist in database routes
([kernel routes](https://github.com/mindverse/Second-Me/blob/d0e40251d9de61b3340b8d0d7d83150669f1885a/lpm_kernel/api/domains/kernel/routes.py)).
Fine-tuned weights make deletion and provenance propagation materially harder.

## Retrieval

The runtime can retrieve L0 and L1 knowledge; document embeddings use Chroma.
GraphRAG is used during data synthesis, not necessarily as the request-time
relationship store. Retrieval and trained knowledge can overlap, making failure
attribution difficult.

## Prompt Construction

Role and knowledge services build context for chat
([prompt builder](https://github.com/mindverse/Second-Me/blob/d0e40251d9de61b3340b8d0d7d83150669f1885a/lpm_kernel/api/domains/kernel2/services/prompt_builder.py)).
There is no business prompt plan with provenance and approval policy.

## Feedback and Evaluation

DPO/data-generation machinery exists, but the reviewed code does not establish a
Good/Bad/Fix reply ledger, safe incremental unlearning, or business evaluation
suite.

## Multi-Tenancy

The local installation and data layout are designed around one personal model.
Roles are not tenants. Multi-workspace isolation would require redesign.

## Business Workflow Support

Absent. The network and role system are identity applications, not sales/support
workflows, campaign policies, opt-outs, or audit.

## Deployment and Operations

Local privacy is a strength, but model training, GPU/Apple acceleration,
embedding storage, Docker services, and model serving impose high burden.
Apache-2.0 permits reuse; base-model licenses must also be checked.

## Strong Ideas

- Keep raw local data separate from derived hierarchy and trained artifacts.
- Make onboarding/data processing an explicit product workflow.
- Support multiple roles over one underlying identity.
- Version synthesized identity representations.

## Weaknesses

- Fine-tuning slows corrections, rollback, provenance, and deletion.
- Single-person assumptions conflict with multi-tenant communication teams.
- Heavy hardware and operational requirements.
- Architecture mixes factual knowledge, identity, and model behavior.

## Relevance to conversation-agent

Use onboarding stages and role overlays as reference. Do not fine-tune in v1 or
adopt a personal-model-per-operator architecture. Prompt-time style and
retrieval are faster, auditable, and provider-independent.

## Decision

- **Adapt later:** onboarding and role overlays.
- **Use as reference:** derived-artifact versioning.
- **Reject for now:** per-user fine-tuning and L2 model lifecycle.

## Evidence

- [Repository architecture and local claim](https://github.com/mindverse/Second-Me/blob/d0e40251d9de61b3340b8d0d7d83150669f1885a/README.md)
- [L2 training pipeline](https://github.com/mindverse/Second-Me/tree/d0e40251d9de61b3340b8d0d7d83150669f1885a/lpm_kernel/L2)
- [Role service](https://github.com/mindverse/Second-Me/blob/d0e40251d9de61b3340b8d0d7d83150669f1885a/lpm_kernel/api/domains/kernel2/services/role_service.py)
- [Knowledge service](https://github.com/mindverse/Second-Me/blob/d0e40251d9de61b3340b8d0d7d83150669f1885a/lpm_kernel/api/domains/kernel2/services/knowledge_service.py)
- [Apache-2.0 license](https://github.com/mindverse/Second-Me/blob/d0e40251d9de61b3340b8d0d7d83150669f1885a/LICENSE)
