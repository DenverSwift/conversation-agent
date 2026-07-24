# Cup Size Versioning

Conversation Agent uses an internal Cup Size progression instead of semantic
milestone versions. The version describes the growth of the agent's personality
and cognitive capabilities, not the amount of code or the size of a release.

The current project release is `AAA.2`.

## How letters work

A change in letters means the project has gained a new cognitive capability.
The progression is:

```text
AAA -> AA -> A -> B -> C -> D -> DD -> E
```

Letters do not represent release stability, code volume, or compatibility.
They identify the agent's current capability stage.

## How subversions work

A numeric suffix records engineering improvements within one cognitive
capability. It does not introduce a new capability.

Examples:

- `AAA.1` is the initial Telegram reply MVP.
- `AAA.2` adds feedback collection and training-data exports.
- `AA.1` introduces the base personality capability.
- `AA.2` improves prompts within that same capability.
- `A.1` introduces per-contact adaptation.

After the final numeric release in one stage, the next cognitive capability
starts at `.1`, such as `AAA.3` followed by `AA.1`.

## Current roadmap

| Cup size | Capability | Example |
| --- | --- | --- |
| `AAA` | Infrastructure | Telegram MVP, feedback, local exports |
| `AA` | Base personality | Stable global Matvey style |
| `A` | Contact adaptation | Per-contact communication profiles |
| `B` | Relationship memory | Durable relationship context |
| `C` | Social intelligence | Advanced tone and situation adaptation |
| `D` | Human-level communication | Broadly natural communication behavior |
| `DD` | Digital clone | High-fidelity reviewed personalization |
| `E` | Experimental future capabilities | Research beyond the defined roadmap |

## Configuration and compatibility

`PROMPT_VERSION` uses the same notation, with `AAA.2` as the current default.
Prompt versions already stored in SQLite remain valid historical strings and
must stay readable; adopting this system does not migrate or rewrite existing
records.

Python package metadata requires a PEP 440-compatible value. The technical
package version `0+aaa.2` encodes the current Cup Size release for packaging
tools, while `[tool.conversation-agent].cup-version = "AAA.2"` in
`pyproject.toml` is the authoritative project version.

Cup Size adoption does not rename Git tags or rewrite Git history.
