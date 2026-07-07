# ADR-0005: Code graph in Postgres with heuristic resolution

**Status:** Accepted (2026-07-07)

## Context

Cross-file questions ("what calls this?", "trace signup end to end", "what breaks if I change X?") need relationship data no chunk contains: imports, containment, inheritance, calls, references. Our traversals are shallow (1–2 hops), per-repo, and read-mostly. The hard part is *edge extraction accuracy*, not graph storage.

## Decision

- **Storage:** `symbols` and `edges` tables in Postgres (edge kinds: `contains`, `imports`, `inherits`, `calls`, `references`; each edge carries a **confidence score**). No new infrastructure.
- **Construction:** tree-sitter query captures + name-resolution heuristics — scope-aware within a file, import-aware across files, qualified-name matching across the repo. Imports/containment/inheritance are high-confidence; call/reference edges are best-effort and scored accordingly.
- **Traversal:** per-repo NetworkX graph hydrated from Postgres on demand and LRU-cached, powering `graph_neighbors` (agent tool) and the graph retrieval channel; recursive CTEs cover rare deeper queries.
- **Role in answers:** the graph is a *recall* device (candidate generation, navigation); it is never sole evidence — reranking and citation verification enforce precision downstream.

## Alternatives considered

- **Neo4j** — powerful traversal language we don't need at 1–2 hops; significant operational + licensing weight.
- **Kuzu (embedded graph DB)** — attractive profile, but young, and an extra dependency for queries Postgres handles.
- **NetworkX pickles only** — no durability, queryability, or incremental update story.
- **SCIP/LSIF indexers for exact edges** — compiler-grade precision, per-language toolchains and often builds; wrong default for arbitrary user repos.

## Consequences

- Zero new infrastructure; graph updates ride the same incremental indexing transaction as everything else.
- Known false/missing call edges — accepted and scored; RISKS #2 tracks it, and the **upgrade path** is an optional SCIP-based indexer per major language emitting the same `edges` schema at confidence 1.0 (additive, Phase 6 candidate).
