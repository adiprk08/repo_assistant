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
| [0010](0010-reranking-disabled-by-default.md) | Reranking disabled by default (measured net-negative) | Accepted |
| [0011](0011-graph-channel-disabled-by-default.md) | Graph retrieval channel disabled by default (measured net-negative on trace/architecture) | Accepted |
| [0012](0012-agentic-loop-opt-in.md) | Agentic reasoning path opt-in (measured parity, single-pass stays default) | Accepted |
| [0013](0013-contextual-descriptions-opt-in.md) | Contextual chunk descriptions opt-in (measured no retrieval lift); summary hierarchy deferred | Accepted |
| [0014](0014-api-service-and-streaming.md) | API service surface; SSE progress via DB polling + chat token streaming via `on_text` | Accepted |
| [0015](0015-conversation-memory.md) | Conversation memory: snapshot-pinned sessions, incremental rolling summary, follow-up condensation | Accepted |
| [0016](0016-api-auth-and-rate-limiting.md) | API-key auth (hashed, DB-backed, bearer) + Redis fixed-window rate limiting (fail-open) | Accepted |
| [0017](0017-web-ui.md) | Minimal Next.js web UI: SSE-over-fetch, localStorage key, GitHub deep-link citations | Accepted |
| [0018](0018-incremental-indexing.md) | Incremental indexing: content-hash diff, copy-forward unchanged, HMAC webhook trigger | Accepted |
| [0019](0019-observability.md) | Observability: OTLP-native OTel traces (Langfuse via OTLP, no vendor SDK) + Prometheus metrics | Accepted |
