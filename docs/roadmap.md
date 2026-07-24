# Roadmap

Conversation Agent follows the Cup Size capability progression defined in
[`versioning.md`](versioning.md).

| Cup size | Capability |
| --- | --- |
| `AAA` | Infrastructure |
| `AA` | Base personality |
| `A` | Contact adaptation |
| `B` | Relationship memory |
| `C` | Social intelligence |
| `D` | Human-level communication |
| `DD` | Digital clone |
| `E` | Experimental future capabilities |

The current released implementation is `AAA.3`.

## Next implementation target: AA.1

AA.1 introduces base personality through runtime few-shot adaptation. It does
not depend on an OpenAI fine-tuning job and does not modify model weights.

Required runtime path:

```text
incoming message
-> global Matvey style profile
-> contact-specific real human examples
-> high-priority relevant Fix corrections
-> recent conversation with provenance
-> configured OpenAI base model
-> Telegram reply
```

AA.1 is complete only when the runtime:

- loads a global style profile;
- reads provider-independent human examples and corrected Fix feedback;
- excludes AI-generated and rejected replies from positive style evidence;
- ranks Fix corrections above ordinary human examples;
- selects a small relevant set for the current message and contact;
- tracks provenance through selection and prompt assembly;
- injects the selected examples before calling the configured base model;
- proves with tests that the full 500-example dataset is not sent per request.

As of AAA.3, feedback collection and JSONL export are implemented. Runtime
loading, relevance ranking, provenance-aware selection, and few-shot prompt
injection are not implemented.

## Longer-term training

Exported datasets remain useful for evaluation, prompt development, runtime
retrieval, and optional future provider-independent or open-weight model
training. Uploading JSONL to OpenAI and receiving a new hosted fine-tuned model
is not a project milestone.
