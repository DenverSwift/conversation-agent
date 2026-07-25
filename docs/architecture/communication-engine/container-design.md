# Container Design

The deployable starts as one API/worker codebase with clear modules. Separate
processes are operational containers, not microservices.

```mermaid
flowchart TB
    subgraph Product["Communication Engine deployment"]
        API["API + control UI"]
        Runtime["Conversation orchestrator"]
        Worker["Job / outbox worker"]
        Compiler["Style and memory compiler worker"]
        Telegram["Telegram adapter"]
        Policy["Policy module"]
        Composer["Retrieval + prompt composer"]
        Eval["Evaluation runner"]
    end

    PG[("PostgreSQL + FTS\noptional pgvector")]
    Blob[("Private object storage")]
    LLM["LLM provider"]
    TG["Telegram"]
    CRM["CRM / support"]
    Queue["PostgreSQL job table\nor managed queue later"]

    API --> Runtime
    Telegram <--> TG
    Telegram --> Runtime
    Runtime --> Policy
    Runtime --> Composer
    Composer --> LLM
    Runtime --> PG
    Worker --> Queue
    Worker --> Telegram
    Compiler --> PG
    Compiler --> Blob
    Eval --> PG
    Runtime <--> CRM
    API --> PG
```

## Modules

- `tenancy`: workspaces, membership, roles, RLS context;
- `identities`: company/operator identities and style versions;
- `contacts`: contacts, consent, do-not-contact;
- `conversations`: normalized messages and provenance;
- `workflows`: sales/support state and timers;
- `policy`: channel/compliance/claim/send decisions;
- `generation`: retrieval, prompt plans, model calls, drafts;
- `approval`: review and escalation;
- `feedback`: corrections and evaluations;
- `delivery`: channel ports, inbox/outbox, attempts;
- `audit`: append-only domain and security events.

Python packages may remain in one repository and transaction boundary. Module
ports prevent provider/channel/storage code from entering domain transitions.
