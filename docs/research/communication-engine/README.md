# Communication Engine Research

This research reframes `conversation-agent` from a single-person Telegram style
assistant into an evolvable, multi-user Communication Engine for sales, support,
and later channels. It is an architecture study only: no production runtime
features were added.

## Start here

1. [Methodology](methodology.md)
2. [Current-system audit](current-system-audit.md)
3. [Comparison matrix](comparison-matrix.md)
4. [Technology landscape](technology-landscape.md)
5. [Findings](findings.md)
6. [Recommendations](recommendations.md)
7. [Risks and unknowns](risks-and-unknowns.md)
8. [Proposed architecture](../../architecture/communication-engine/README.md)

## Mandatory project reports

- [soul.md](projects/soul-md.md)
- [LangMem](projects/langmem.md)
- [Mem0](projects/mem0.md)
- [Letta / MemGPT and LettaBot](projects/letta.md)
- [Graphiti / Zep](projects/graphiti-zep.md)
- [Supermemory](projects/supermemory.md)
- [SillyTavern](projects/sillytavern.md)
- [Second Me](projects/second-me.md)
- [Podisen](projects/podisen.md)
- [WeClone and CloneLLM](projects/weclone-clonellm.md)
- [Additional findings](projects/additional-projects.md)

## Bottom line

No researched system is the target product. Memory frameworks solve memory;
character systems solve prompt/persona composition; clone projects solve
dataset/model personalization; support/sales tools solve workflow slices. The
differentiated product is their controlled composition with message provenance,
identity/style separation, deterministic business state, human approval, and
tenant-safe channel delivery.
