# Risks and Unknowns

## Highest risks

| Risk | Consequence | Mitigation |
| --- | --- | --- |
| Cross-tenant retrieval or prompt leak | Severe privacy incident | PostgreSQL RLS, tenant in every key, negative isolation tests, no caller-only filtering |
| Duplicate or prohibited send | Spam, ToS and reputation damage | Inbox/outbox idempotency, policy decision record, unique external keys, kill switch |
| AI output enters human style evidence | Progressive style drift | Immutable provenance, allow-list evidence policy, compiler tests |
| Hallucinated service claims | Customer harm | Approved knowledge facts, claim allow-list, approval for offers/pricing, evaluation |
| Stale/contradictory memory | Wrong personalization | validity intervals, confidence, source links, correction workflow |
| Prompt injection from contact/content | Policy bypass or exfiltration | delimit untrusted evidence, policy before generation and before send, tool allow-lists |
| Fine-tuning on private chats | deletion/consent/licensing risk | postpone; explicit consent, reviewed corpus, open-weight ADR if revisited |
| Graph/vector overengineering | slow delivery and operational burden | PostgreSQL first; promote only after benchmark thresholds |

## Open technical evidence

- Hosted Mem0/Zep/Supermemory tenant isolation and data residency were not
  established from OSS source.
- Real private-data recall, style fidelity, hallucination, and negative-transfer
  benchmarks have not been run across providers.
- Telegram account automation and later WhatsApp/LinkedIn use require a separate
  current ToS/legal review before auto-send.
- Optimal grouping thresholds differ by channel and must be evaluated from
  fixtures, not inherited from Podisen or the current three-minute rule.
- PostgreSQL FTS may be enough for v1; pgvector inclusion depends on measured
  recall gain and operational budget.
- Whether long-running campaign timers justify Temporal remains unknown.
- The UI choice (custom control panel vs Chatwoot/CRM integration) remains open.

## Explicit non-claims

This research does not claim human-level imitation, legal permission for mass
outbound communication, hosted-service security, or that reported third-party
benchmark rankings reproduce on our data.
