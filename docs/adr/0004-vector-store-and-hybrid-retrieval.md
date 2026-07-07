# ADR-0004: Qdrant + native hybrid retrieval + cross-encoder reranking

**Status:** Accepted (2026-07-07)

## Context

Code questions split into two modes: semantic ("where is authentication handled?") and lexical (exact identifiers, error strings). Dense-only retrieval misses rare identifiers; BM25-only misses concepts. We need first-class hybrid retrieval, metadata filtering (repo/language/path/kind), and a store that runs fine in Docker on a laptop yet scales to millions of chunks.

## Decision

- **Qdrant** as the vector store: named vectors per point — `dense` (voyage-code-3) + `sparse` (BM25 via FastEmbed) — with **server-side hybrid queries fused by RRF**; payload indexes on `repo_id`, `language`, `kind`, `path`; scalar int8 quantization at the Large tier.
- **Third retrieval channel outside Qdrant:** exact + trigram-fuzzy symbol lookup in Postgres (pg_trgm) for identifier-bearing queries, fused with the Qdrant channels by RRF in `retrieval/`.
- **Reranking:** cross-encoder over fused top ~50 → top 12, behind a `Reranker` interface — default Voyage `rerank-2.5`, local fallback `bge-reranker-v2-m3`.
- **`VectorIndex` interface** wraps Qdrant so a pgvector backend remains implementable.

## Alternatives considered

- **Postgres + pgvector** — the fewest moving parts (one database), and genuinely adequate for dense-only at our scale; but hybrid means bolting on `tsvector` FTS with manual score fusion, and HNSW + FTS tuning in one OLTP instance couples concerns. Kept as the documented fallback backend.
- **LanceDB** — lovely embedded DX; concurrent multi-process service story (API + workers writing) is weaker.
- **Elasticsearch/OpenSearch + kNN** — best-in-class lexical, heavy operational footprint for a solo-run system.
- **Milvus / Weaviate** — capable, heavier ops profile; no decisive advantage over Qdrant for this workload.

## Consequences

- One store serves both retrieval modes with one query; laptop-friendly (single container) and production-plausible (quantization, snapshots).
- RRF avoids score-calibration fragility across channels; reranker carries precision so channel recall can stay generous.
- Second datastore to operate (accepted; see RISKS #11) — mitigated by the interface + pgvector fallback.
