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

The current released implementation is `AA.2`.

## AA.1: Base personality

AA.1 means: **Base personality through persistent runtime behavior rules and
dynamic human example injection.** It does not depend on an OpenAI fine-tuning
job and does not modify model weights.

The implemented AA.1 Telegram Human Agent flow also makes this personality
operational in inbound business conversations: messages are accumulated and
analyzed, a goal and structured reply are proposed, and a private Trainer Bot
must approve or correct the behavior plan before delivery. See
[`aa1-telegram-human-agent-mvp.md`](aa1-telegram-human-agent-mvp.md).

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

The AA.1 runtime:

- loads a global style profile;
- reads provider-independent human examples and corrected Fix feedback;
- excludes AI-generated and rejected replies from positive style evidence;
- ranks Fix corrections above ordinary human examples;
- selects a small relevant set for the current message and contact;
- tracks provenance through selection and prompt assembly;
- injects the selected examples before calling the configured base model;
- proves with tests that the full 500-example dataset is not sent per request.

## AA.2: Incremental Style Compiler

AA.2 preserves the AA.1 cognitive capability while improving its local
engineering workflow. Every source gets a stable identity and deterministic
SHA-256 content hash. Structured observations are cached in private,
device-specific SQLite state.

- unchanged sources consume no new analysis requests;
- new and modified sources are the only raw evidence sent for analysis;
- duplicate content under another source key reuses existing analysis while
  increasing evidence counts;
- deleted and modified old sources lose their previous contributions;
- final rules are deterministically regenerated from all currently valid
  cached observations;
- analyzer fingerprint changes require an explicit `--full-rebuild`.

## Longer-term training

Exported datasets remain useful for evaluation, prompt development, runtime
retrieval, and optional future provider-independent or open-weight model
training. Uploading JSONL to OpenAI and receiving a new hosted fine-tuned model
is not a project milestone.
