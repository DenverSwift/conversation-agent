# ADR 0001: Runtime Style Adaptation

- Status: Accepted
- Date: 2026-07-24

## Context

Conversation Agent collects explicit feedback and exports provider-independent
JSONL datasets. Earlier architectural language left open a path where those
files could be uploaded to OpenAI and used to create a hosted fine-tuned model.
That is no longer a valid project roadmap.

OpenAI's official deprecation schedule says self-service fine-tuning is being
wound down: organizations without prior fine-tuning access cannot create jobs,
and active existing customers lose the ability to create new jobs on
2027-01-06. Existing fine-tuned inference lasts only until the corresponding
base model is deprecated.

Source:
[OpenAI API deprecations](https://developers.openai.com/api/docs/deprecations#update-to-openais-self-serve-fine-tuning).

The current AAA.3 runtime loads global behavior from README and up to 30 recent
Telegram messages. It does not load exported examples or Fix corrections.

## Decision

AA.1 style adaptation will use runtime few-shot retrieval:

```text
incoming message
-> global Matvey style profile
-> contact-specific real human examples
-> high-priority relevant Fix corrections
-> recent conversation with provenance
-> configured OpenAI base model
-> Telegram reply
```

The retriever will select only a small relevant set. It will not send the full
500-example export on every request. AI-generated replies and rejected replies
cannot be positive style evidence. Corrected Fix replies are human-authored and
receive the highest retrieval priority. Model weights are not modified.

Feedback collection and JSONL export remain separate provider-independent
capabilities. Export does not imply upload or training.

## Consequences

- Style improvements can ship without access to a provider training service.
- The runtime needs example loading, relevance ranking, contact filtering,
  provenance, token budgeting, and prompt assembly.
- Selection behavior must be deterministic enough to test and inspect.
- Privacy review applies to every example selected for a request.
- Exported approved AI replies may support evaluation, but cannot be indexed as
  Matvey style evidence.
- Retrieval quality and prompt quality become runtime engineering concerns.

## Future options

Reviewed provider-independent datasets may later support evaluation, prompt
development, or explicitly approved training of an open-weight model. Such
training would be a separate architecture decision with its own privacy,
hardware, licensing, evaluation, and deployment requirements.
