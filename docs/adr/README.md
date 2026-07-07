# Architecture Decision Records

Format: Status / Context / Decision / Alternatives considered / Consequences. ADRs are immutable — supersede, don't rewrite. Policy in [docs/README.md](../README.md).

| # | Decision | Status |
|---|---|---|
| [0001](0001-language-and-stack.md) | Python 3.12, FastAPI, library-first core | Accepted |
| [0002](0002-parsing-and-chunking.md) | tree-sitter parsing, AST-aware chunking | Accepted |
| [0003](0003-embedding-strategy.md) | voyage-code-3 embeddings behind a pluggable interface | Accepted |
| [0004](0004-vector-store-and-hybrid-retrieval.md) | Qdrant with native hybrid retrieval + cross-encoder rerank | Accepted |
| [0005](0005-code-graph.md) | Code graph in Postgres with heuristic resolution | Accepted |
| [0006](0006-reasoning-pipeline.md) | Two-tier reasoning: routed fast RAG / budgeted agent | Accepted |
| [0007](0007-llm-provider-and-models.md) | Anthropic API, tiered models, prompt caching, native citations | Accepted |
| [0008](0008-job-queue.md) | arq + Redis with checkpointed pipeline stages | Accepted |
| [0009](0009-multitenancy-and-versioning.md) | Payload-partitioned multitenancy; commit-pinned snapshots | Accepted |
