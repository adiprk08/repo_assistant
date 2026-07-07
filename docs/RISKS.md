# Risk register

> Reviewed at each phase close. L/I = likelihood / impact (H/M/L).

| # | Risk | L | I | Mitigation |
|---|------|---|---|------------|
| 1 | **Indexing cost blowup on large repos** (embeddings + enrichment LLM calls) | H | H | Tiered enrichment by repo size ([ARCHITECTURE §9](ARCHITECTURE.md)); content-hash embedding cache; haiku for all enrichment; hard per-repo cost budgets in config; cost recorded per snapshot |
| 2 | **Heuristic call-graph precision** — wrong edges mislead trace answers | H | M | Confidence scores on edges; graph is a *candidate channel*, never sole evidence — reranker + citation verification filter bad hops; SCIP-based precise resolution is the documented upgrade path (ADR-0005) |
| 3 | **Hallucinated or stale citations** | M | H | API-native citations + mechanical post-hoc verification against the index; answers pinned to commit SHA; citation validity is a CI gate |
| 4 | **Prompt injection via repo content** (malicious README instructs the agent) | M | H | Repo text fenced as data; read-only index tools (no FS, no network, no mutation); no code execution; Phase 5 red-team pass; secret redaction at scan time |
| 5 | **Retrieval quality plateaus / regressions go unnoticed** | M | H | Eval harness with CI gates lands *before* tuning (Phase 2); ablation flags keep every technique's contribution measured |
| 6 | **LLM judge drift** makes eval trends untrustworthy | M | M | Human-graded anchor set + κ agreement check on judge changes; judge prompt + model pinned per run and recorded |
| 7 | **Provider dependency** (Anthropic/Voyage pricing, deprecation, outage) | L | M | All providers behind interfaces with a working local fallback (BGE-M3 embeddings, local reranker); model IDs are config; degraded local mode acceptable for dev/demo |
| 8 | **Latency of agentic path** frustrates interactive use | M | M | Router sends only multi-hop queries to the agent; streaming + progress narration; tool budget caps; prompt caching for the stable prefix |
| 9 | **Windows dev environment friction** (tree-sitter builds, path handling) | M | L | `tree-sitter-language-pack` ships prebuilt wheels; pathlib + POSIX-normalized repo paths in the index; CI runs Linux to match deployment |
| 10 | **Scope creep** — breadth of "assistant" ambitions stalls depth | M | M | Phase gates with exit criteria ([ROADMAP.md](ROADMAP.md)); Phase 6 parking lot for extensions; vertical-slice-first rule |
| 11 | **Qdrant operational surface** (backups, upgrades) for a solo project | L | M | Single-node with volume snapshots is adequate at this scale; `VectorIndex` interface keeps a pgvector fallback viable if ops budget shrinks |
| 12 | **Concurrency bugs between incremental updates and live chat** | M | M | Snapshot semantics: chat reads only the active snapshot; updates build against a new snapshot and switch atomically; deletes deferred until the switch |

## Top three, expanded

**Cost (1):** the single most common failure mode of RAG-over-code projects is an indexing bill that makes iteration impossible. Defenses are structural: enrichment is *opt-in per tier*, embedding is cached by content hash (re-index of an unchanged repo costs ~zero), and the eval harness reports cost per point of recall so we can prove which spend is worth it.

**Graph precision (2):** we deliberately accept imprecise call edges rather than requiring per-language compiler tooling. The design treats the graph as a recall device (surfacing candidates) while precision is enforced downstream (rerank + citation verification). If eval shows trace-category answers capped by graph noise, ADR-0005's SCIP upgrade path is the planned response — an additive indexer, not a redesign.

**Injection (4):** the agent acts only through read-only tools over an immutable index; even a fully "convinced" model cannot mutate state, exfiltrate secrets (they're never indexed), or execute code. Residual risk is answer manipulation, which citation verification and the Phase 5 red-team exercise target.
