# ADR-0009: Payload-partitioned multitenancy and commit-pinned snapshots

**Status:** Accepted (2026-07-07)

## Context

One deployment serves many repositories, each possibly re-indexed many times. Answers must be reproducible ("true as of commit X"), incremental updates must not corrupt live chats, and the vector store must not degrade as repo count grows.

## Decision

- **Multitenancy:** a **single Qdrant collection** partitioned by an indexed `repo_id` payload field (Qdrant's recommended multitenancy pattern), not collection-per-repo. Postgres rows are likewise scoped by `repo_id`, enforced at the storage layer so no query can cross tenants.
- **Snapshots:** every indexed artifact (point payloads, symbols, edges, summaries) is stamped with `commit_sha`. A repo has exactly one **active snapshot** in v1; the `snapshots` table plus commit-stamped data makes multiple refs/time-travel an additive feature, not a redesign.
- **Update isolation:** incremental updates build against the incoming snapshot; chat sessions bind to the snapshot current at session start; the active pointer flips atomically when indexing completes, and superseded rows/points are garbage-collected after a grace period.
- **Version awareness in answers:** responses state the commit they describe; citations embed it (`path:lines@commit`).

## Alternatives considered

- **Collection-per-repo in Qdrant** — hard isolation, but collections carry per-collection overhead and cold-start cost; explicitly discouraged by Qdrant for many-tenant setups.
- **Full re-index per update, no snapshots** — simplest, but makes updates minutes-to-hours, breaks in-flight chats, and wastes embedding spend the cache is designed to save.
- **Immutable snapshot-per-commit retention** — beautiful for time-travel, unbounded storage; deferred until a concrete need (the schema already permits it).

## Consequences

- Predictable resource usage as repos scale; tenant filters are on the hot path and covered by payload/DB indexes (tested).
- Slightly more bookkeeping (snapshot lifecycle, GC) — concentrated in `indexing/` and exercised by integration tests.
