# Repo Assistant

An intelligent GitHub Repository Assistant powered by Retrieval-Augmented Generation.

Point it at a repository and interact with it in natural language. Unlike keyword search or naive "chat with your code" tools, Repo Assistant builds a **structured understanding** of the codebase — a symbol-level index, a cross-file code graph, and hierarchical summaries — and combines hybrid retrieval with agentic reasoning to answer questions with **verifiable source citations**.

## What it does

- **Explain unfamiliar codebases** — architecture overviews, module summaries, onboarding walkthroughs
- **Locate functionality** — "where is rate limiting implemented?" resolved to exact files and lines
- **Trace execution flow** — follow a request across files, functions, and layers
- **Answer implementation questions** — grounded in the actual code at a specific commit, with citations
- **Assist debugging** — reason about behavior using real definitions, call sites, and configuration
- **Generate documentation** — module docs and summaries derived from source, not guesses

## How it works (at a glance)

```
GitHub repo ──▶ Ingestion ──▶ Indexing ──▶ Retrieval ──▶ Reasoning ──▶ Cited answer
                (clone,        (embeddings,  (hybrid:      (fast RAG or
                 tree-sitter    BM25, code    dense+sparse  agentic loop
                 parse, AST     graph,        +symbol,      with code-
                 chunking)      summaries)    rerank)       reading tools)
```

Every answer cites `file:line-range` at a pinned commit, and citations are verified against the index before being shown.

## Status

**Phase 0 — architecture and planning complete; implementation starting.** See [docs/ROADMAP.md](docs/ROADMAP.md) for the phased plan.

## Documentation

| Document | Contents |
|---|---|
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System design, module responsibilities, data flow, data model, pipelines |
| [docs/ROADMAP.md](docs/ROADMAP.md) | Phased roadmap with milestones and exit criteria |
| [docs/EVALUATION.md](docs/EVALUATION.md) | Evaluation methodology, benchmarks, metrics, CI gates |
| [docs/RISKS.md](docs/RISKS.md) | Risk register and mitigations |
| [docs/adr/](docs/adr/README.md) | Architecture Decision Records — why each technology was chosen |

## Planned stack

| Concern | Choice | Decision record |
|---|---|---|
| Language / service | Python 3.12, FastAPI, library-first core | [ADR-0001](docs/adr/0001-language-and-stack.md) |
| Parsing / chunking | tree-sitter, AST-aware chunking | [ADR-0002](docs/adr/0002-parsing-and-chunking.md) |
| Embeddings | voyage-code-3 (pluggable; local fallback) | [ADR-0003](docs/adr/0003-embedding-strategy.md) |
| Vector store / retrieval | Qdrant, native hybrid dense+sparse, RRF, cross-encoder rerank | [ADR-0004](docs/adr/0004-vector-store-and-hybrid-retrieval.md) |
| Code graph | Postgres edges + in-memory traversal | [ADR-0005](docs/adr/0005-code-graph.md) |
| Reasoning | Router → fast RAG or budgeted agentic loop | [ADR-0006](docs/adr/0006-reasoning-pipeline.md) |
| LLM | Anthropic API — claude-opus-4-8 + claude-haiku-4-5, prompt caching, native citations | [ADR-0007](docs/adr/0007-llm-provider-and-models.md) |
| Jobs | arq + Redis, checkpointed pipeline stages | [ADR-0008](docs/adr/0008-job-queue.md) |
| Multitenancy / versioning | Payload-partitioned index, commit-pinned snapshots | [ADR-0009](docs/adr/0009-multitenancy-and-versioning.md) |

## License

TBD (to be added before first public release).
