# WeClone and CloneLLM

## Snapshot

| Field | WeClone | CloneLLM |
| --- | --- | --- |
| Repository | [xming521/WeClone](https://github.com/xming521/WeClone) | [msamsami/CloneLLM](https://github.com/msamsami/CloneLLM) |
| Commit | [`26eefcc981e6fdaaf6f1dae7f625cce221795f14`](https://github.com/xming521/WeClone/tree/26eefcc981e6fdaaf6f1dae7f625cce221795f14) | [`dac1f0ff7c237aef9131dfcab82fabcb31af6293`](https://github.com/msamsami/CloneLLM/tree/dac1f0ff7c237aef9131dfcab82fabcb31af6293) |
| Researched | 2026-07-25 | 2026-07-25 |
| License | AGPL-3.0 | MIT |
| Primary language | Python | Python |
| Activity | Active; commit 2026-06-27 | Moderate; commit 2025-06-12 |
| Delivery | Local dataset/training/deployment tool | Embeddable Python library |
| Maturity | End-to-end research tool | Small tested library |
| Purpose | SFT/continued training from chat history | Prompt-time profile plus static/RAG context |

## Architecture

WeClone parses exported chat data, groups consecutive messages, builds
multi-turn Q/A examples, optionally scores/cleans them with an LLM, runs
continued pre-training/SFT (LoRA via LLaMA-Factory), evaluates, and serves an
OpenAI-compatible model. Its central dataset logic is
[`qa_generator.py`](https://github.com/xming521/WeClone/blob/26eefcc981e6fdaaf6f1dae7f625cce221795f14/weclone/data/qa_generator.py).

CloneLLM is a LangChain/LiteLLM library. `CloneLLM.fit()` either summarizes
documents into static context or embeds them in InMemory/FAISS/Chroma, then
combines retrieved context, a typed `UserProfile`, optional history, and system
prompts
([core](https://github.com/msamsami/CloneLLM/blob/dac1f0ff7c237aef9131dfcab82fabcb31af6293/src/clonellm/core.py)).

## Identity and Style

WeClone puts style into weights; the dataset records original message ID,
sender direction, talker, and time
([models](https://github.com/xming521/WeClone/blob/26eefcc981e6fdaaf6f1dae7f625cce221795f14/weclone/data/models.py)).
CloneLLM uses explicit profile fields, Big Five traits, and communication
samples
([models](https://github.com/msamsami/CloneLLM/blob/dac1f0ff7c237aef9131dfcab82fabcb31af6293/src/clonellm/models.py)).
Neither implements a compiled, versioned, source-provenanced per-contact style.

## Memory Model

WeClone has model weights and recent chat, not factual memory. CloneLLM has
bounded in-process message history and document context, not durable fact/event
or temporal memory.

## Retrieval

WeClone does not require runtime retrieval for learned style. CloneLLM retrieves
top-k document chunks from a vector store; it lacks lexical fusion, metadata
tenancy, temporal handling, and business-aware reranking.

## Prompt Construction

WeClone runtime uses a default prompt plus history. CloneLLM has a fixed ordered
template for clone instruction, profile, context, history, and question
([prompt source](https://github.com/msamsami/CloneLLM/blob/dac1f0ff7c237aef9131dfcab82fabcb31af6293/src/clonellm/_prompt.py)).
Its instruction to conceal AI status is unacceptable for our business product.

## Feedback and Evaluation

WeClone includes PII tests, model test datasets, and a currently TODO evaluation
CLI. It can clean examples with an LLM. CloneLLM has good unit coverage of
retrieval/history but no quality feedback loop. Neither prevents reviewed AI
outputs from contaminating later training unless the caller curates the corpus.

## Multi-Tenancy

Absent in both. They instantiate a clone, not a workspace with roles and
database-enforced ownership.

## Business Workflow Support

Absent. Messenger ingestion/deployment is not a sales/support state machine.

## Deployment and Operations

WeClone needs training hardware and inherits AGPL obligations. Updates,
unlearning, and per-operator models are operationally expensive. CloneLLM is MIT
and light, but its in-memory defaults and generic RAG are insufficient for
production.

## Strong Ideas

- WeClone: deterministic sender direction, message IDs, grouping, PII detector,
  and multimodal-aware dataset pipeline.
- CloneLLM: typed profile plus communication samples and optional provider-neutral
  RAG.
- The contrast demonstrates why facts/context and style behavior should not be
  forced into one mechanism.

## Weaknesses

- Fine-tuned weights weaken provenance, deletion, incremental correction, and
  per-contact variation.
- Prompt-only CloneLLM lacks durable memory, tenant isolation, and workflow.
- Both use "clone" semantics that encourage undisclosed impersonation.

## Relevance to conversation-agent

Continue prompt-time conditioning for v1. Retain provider-independent datasets
for future open-weight experiments only after evaluation, privacy approval, and
AI-authorship exclusion. Adapt typed communication samples, not CloneLLM's
concealment prompt.

## Decision

- **Adapt now:** typed communication examples and deterministic dataset fields.
- **Use as reference:** WeClone's training pipeline for future experiments.
- **Reject for now:** fine-tuning, per-user model serving, and clone concealment.

## Evidence

- [WeClone dataset generator](https://github.com/xming521/WeClone/blob/26eefcc981e6fdaaf6f1dae7f625cce221795f14/weclone/data/qa_generator.py)
- [WeClone PII detector](https://github.com/xming521/WeClone/blob/26eefcc981e6fdaaf6f1dae7f625cce221795f14/weclone/core/PII/pii_detector.py)
- [WeClone training](https://github.com/xming521/WeClone/blob/26eefcc981e6fdaaf6f1dae7f625cce221795f14/weclone/train/train_sft.py)
- [CloneLLM core](https://github.com/msamsami/CloneLLM/blob/dac1f0ff7c237aef9131dfcab82fabcb31af6293/src/clonellm/core.py)
- [CloneLLM prompt](https://github.com/msamsami/CloneLLM/blob/dac1f0ff7c237aef9131dfcab82fabcb31af6293/src/clonellm/_prompt.py)
- [WeClone AGPL-3.0](https://github.com/xming521/WeClone/blob/26eefcc981e6fdaaf6f1dae7f625cce221795f14/LICENSE)
- [CloneLLM MIT](https://github.com/msamsami/CloneLLM/blob/dac1f0ff7c237aef9131dfcab82fabcb31af6293/LICENSE)
