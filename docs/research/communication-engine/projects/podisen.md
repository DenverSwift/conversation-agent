# Podisen

## Snapshot

| Field | Value |
| --- | --- |
| Repository | [geethikaisuru/podisen-whatsapp-clone](https://github.com/geethikaisuru/podisen-whatsapp-clone) |
| Commit | [`457210e49a6c137157f477d83f0d9b55f493ee87`](https://github.com/geethikaisuru/podisen-whatsapp-clone/tree/457210e49a6c137157f477d83f0d9b55f493ee87) |
| Researched | 2026-07-25 |
| License | **Unclear**: README says MIT, but the commit contains no `LICENSE` file |
| Primary language | Python/Jupyter |
| Activity | Low; researched commit dated 2025-05-12 |
| Delivery | Data scripts plus Flask/WhatsApp Cloud API bot |
| Maturity | Prototype |
| Purpose | Fine-tune a personal model from exported WhatsApp chats |

## Architecture

The official-looking repository found for the requested name contains a
four-stage data folder, notebook/scripts, and one Flask webhook. The processor
parses WhatsApp text, groups messages by a one-hour gap, asks Gemini to convert
each group to JSON, cleans/fixes roles, and splits train/eval
([processor](https://github.com/geethikaisuru/podisen-whatsapp-clone/blob/457210e49a6c137157f477d83f0d9b55f493ee87/0-data-processing/01.process_LLM.py)).
Runtime uses an in-memory per-phone history and a Vertex endpoint
([`app.py`](https://github.com/geethikaisuru/podisen-whatsapp-clone/blob/457210e49a6c137157f477d83f0d9b55f493ee87/app.py)).

## Identity and Style

The account owner's name maps messages to `model`; all others map to `user`.
Sequential same-speaker messages are combined and slang/emojis preserved. Style
is embedded through fine-tuning, with no editable rulebook, source provenance,
per-contact adaptation, or rollback.

## Memory Model

Only bounded in-process conversation history exists at runtime. Restart loses
it. There is no factual, episodic, temporal, or relationship store.

## Retrieval

Absent. All personalization is expected from model weights and recent history.

## Prompt Construction

A fixed system instruction names the owner and asks for casual conversation.
There is no versioning, token plan, policies, or evidence selection.

## Feedback and Evaluation

The notebook validates JSONL, asks for manual sample review, and makes a random
80/20 split. There is no leakage-aware split, Good/Bad/Fix feedback, or
provenance-safe incremental update.

## Multi-Tenancy

Absent. The global model endpoint and process memory are not workspace isolated.

## Business Workflow Support

Absent. The webhook auto-sends every generated reply and even disables several
model safety thresholds in code. There is no idempotency, signature validation
shown, opt-out, approval, audit, or duplicate-send protection.

## Deployment and Operations

Requires WhatsApp Business API, GCP/Vertex, Cloud Run, and fine-tuning. The
in-memory history is not horizontally scalable. Logging full webhook and
generated text creates a privacy risk. The missing license file means code
reuse is not legally safe without clarification.

## Strong Ideas

- Parse multiline exports and preserve message fragments.
- Distinguish account-owner messages before dataset construction.
- Keep multi-turn examples rather than reducing everything to one Q/A pair.

## Weaknesses

- LLM-based role/data conversion adds non-determinism where parsing can be
  deterministic.
- One-hour grouping is too coarse and lacks stable message provenance.
- Unsafe auto-send, full-content logs, in-memory state, and no deduplication.
- No license file, tests, migrations, or production isolation.

## Relevance to conversation-agent

The dataset-pairing lessons validate our deterministic exporter, but our current
three-minute same-side grouping, AI-message exclusion, and source IDs are
stronger. No runtime code should be reused.

## Decision

- **Use as reference:** multi-part conversation pairing problem.
- **Reject:** code reuse, fine-tuning pipeline, and runtime architecture.

## Evidence

- [Data processor](https://github.com/geethikaisuru/podisen-whatsapp-clone/blob/457210e49a6c137157f477d83f0d9b55f493ee87/0-data-processing/01.process_LLM.py)
- [Cleaning script](https://github.com/geethikaisuru/podisen-whatsapp-clone/blob/457210e49a6c137157f477d83f0d9b55f493ee87/0-data-processing/02.clean_jsonl.py)
- [Runtime webhook](https://github.com/geethikaisuru/podisen-whatsapp-clone/blob/457210e49a6c137157f477d83f0d9b55f493ee87/app.py)
- [README and unsupported MIT claim](https://github.com/geethikaisuru/podisen-whatsapp-clone/blob/457210e49a6c137157f477d83f0d9b55f493ee87/README.md)
